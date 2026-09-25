"""Idempotent SQLite -> V2 database import (the existing agency installation)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from rma_portal.config import Settings
from rma_portal.infrastructure.db.session import create_engine_for
from rma_portal.infrastructure.db.sqlite_import import (
    IMPORT_ORDER,
    SqliteImportError,
    import_sqlite_database,
    upgrade_to_head,
)
from tests.test_migrations import _run_upgrade
from tests.test_migrations_v2 import PRE_V2_REVISION, seed_legacy_installation


def _legacy_source(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    source_dir = tmp_path / "legacy"
    source_dir.mkdir()
    _run_upgrade(source_dir, PRE_V2_REVISION)
    source = source_dir / "rma_portal.sqlite3"
    return source, seed_legacy_installation(source)


def _target(tmp_path: Path, name: str = "target"):
    settings = Settings(data_dir=tmp_path / name)
    settings.data_dir.mkdir(parents=True)
    upgrade_to_head(settings.database_url)
    return create_engine_for(settings)


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"select count(*) from {table}")).scalar_one()
            for table in IMPORT_ORDER
        }


def test_import_copies_every_dossier_alert_read_note_and_work_status(tmp_path: Path):
    source, ids = _legacy_source(tmp_path)
    engine = _target(tmp_path)
    try:
        report = import_sqlite_database(source, engine)

        counts = _counts(engine)
        assert counts["users"] == 2
        assert counts["dossiers"] == 3
        assert counts["notifications"] == 2
        assert counts["notification_reads"] == 1
        assert counts["dossier_notes"] == 1
        assert counts["workflow_occurrences"] == 4
        assert counts["workflow_work"] == 3
        assert report.total_inserted() > 0

        with engine.connect() as conn:
            # Legacy primary keys are preserved so every foreign key still resolves.
            read = conn.execute(text("select notification_id, user_id from notification_reads")).one()
            assert tuple(read) == (ids["n_new"], ids["alice"])
            work = conn.execute(
                text(
                    "select w.status, w.version from workflow_work w "
                    "join workflow_memberships m on m.id = w.membership_id where m.dossier_id = :d"
                ),
                {"d": ids["d2"]},
            ).one()
            assert tuple(work) == ("IN_PROGRESS", 4)
    finally:
        engine.dispose()


def test_import_is_idempotent(tmp_path: Path):
    source, _ = _legacy_source(tmp_path)
    engine = _target(tmp_path)
    try:
        import_sqlite_database(source, engine)
        first = _counts(engine)

        second_report = import_sqlite_database(source, engine)

        assert _counts(engine) == first
        assert second_report.total_inserted() == 0
    finally:
        engine.dispose()


def test_import_does_not_modify_the_source_database(tmp_path: Path):
    source, _ = _legacy_source(tmp_path)
    before = source.read_bytes()
    engine = _target(tmp_path)
    try:
        import_sqlite_database(source, engine)
    finally:
        engine.dispose()
    assert source.read_bytes() == before


def test_import_restores_baseline_state_seeded_target_rows_would_lose(tmp_path: Path):
    """The target migration seeds the account/workflow; the import must update, not skip, them."""
    source, _ = _legacy_source(tmp_path)
    import sqlite3

    con = sqlite3.connect(source)
    con.execute(
        "update portal_accounts set baseline_completed_at = '2026-01-02 03:04:05.000000', "
        "last_poll_at = '2026-03-01 09:00:00.000000'"
    )
    con.commit()
    con.close()
    engine = _target(tmp_path)
    try:
        import_sqlite_database(source, engine)
        with engine.connect() as conn:
            baseline = conn.execute(text("select baseline_completed_at from portal_accounts")).scalar_one()
        assert str(baseline).startswith("2026-01-02")
    finally:
        engine.dispose()


def test_missing_source_is_reported_clearly(tmp_path: Path):
    engine = _target(tmp_path)
    try:
        try:
            import_sqlite_database(tmp_path / "absent.sqlite3", engine)
        except SqliteImportError as exc:
            assert "introuvable" in str(exc)
        else:
            raise AssertionError("expected SqliteImportError")
    finally:
        engine.dispose()
