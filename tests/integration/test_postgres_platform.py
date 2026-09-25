"""PostgreSQL acceptance checks. Skipped unless ``RMA_TEST_POSTGRES_URL`` is set.

Point it at an *empty, disposable* database, for example the compose stack's
PostgreSQL bound to localhost:

    RMA_TEST_POSTGRES_URL=postgresql+psycopg://rma:secret@127.0.0.1:5432/rma_test \
        uv run pytest tests/integration/test_postgres_platform.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from rma_portal.infrastructure.db.sqlite_import import (
    IMPORT_ORDER,
    import_sqlite_database,
    upgrade_to_head,
)
from tests.test_migrations import _run_upgrade
from tests.test_migrations_v2 import PRE_V2_REVISION, seed_legacy_installation

POSTGRES_URL = os.environ.get("RMA_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="RMA_TEST_POSTGRES_URL is not set")


@pytest.fixture
def pg_engine():
    engine = create_engine(POSTGRES_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(POSTGRES_URL)
    yield engine
    engine.dispose()


def _legacy(tmp_path: Path):
    source_dir = tmp_path / "legacy"
    source_dir.mkdir()
    _run_upgrade(source_dir, PRE_V2_REVISION)
    source = source_dir / "rma_portal.sqlite3"
    return source, seed_legacy_installation(source)


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"select count(*) from {t}")).scalar_one() for t in IMPORT_ORDER}


def test_fresh_postgres_schema_has_seed_and_immutability_triggers(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(text("select key from workflows")).scalars().all() == ["agreement_garage"]
    with pytest.raises(DBAPIError, match="immutable"), pg_engine.begin() as conn:
        workflow_id = conn.execute(text("select id from workflows")).scalar_one()
        conn.execute(
            text(
                "insert into workflow_events (workflow_id, kind, notification_class, detected_at, "
                "changed_fields_json, before_fingerprints_json, after_fingerprints_json) "
                "values (:w, 'WORKFLOW_ITEM_NEW', 'ACTION', now(), '[]', '{}', '{}')"
            ),
            {"w": workflow_id},
        )
        conn.execute(text("update workflow_events set message = 'rewritten'"))


def test_legacy_sqlite_import_is_complete_idempotent_and_fixes_sequences(pg_engine, tmp_path):
    source, ids = _legacy(tmp_path)

    import_sqlite_database(source, pg_engine)
    first = _counts(pg_engine)
    second_report = import_sqlite_database(source, pg_engine)

    assert first["dossiers"] == 3 and first["notifications"] == 2
    assert first["workflow_occurrences"] == 4 and first["workflow_work"] == 3
    assert _counts(pg_engine) == first
    assert second_report.total_inserted() == 0
    with pg_engine.begin() as conn:
        read = conn.execute(text("select notification_id, user_id from notification_reads")).one()
        assert tuple(read) == (ids["n_new"], ids["alice"])
        # Sequences moved past imported ids: a new user must not collide.
        new_id = conn.execute(
            text(
                "insert into users (username, display_name, password_hash, role, active, created_at)"
                " values ('carol', 'Carol', 'h', 'EMPLOYEE', true, now()) returning id"
            )
        ).scalar_one()
        assert new_id > max(ids["alice"], ids["bob"])
