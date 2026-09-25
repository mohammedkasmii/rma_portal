"""PostgreSQL acceptance checks. Skipped unless ``RMA_TEST_POSTGRES_URL`` is set.

Point it at an *empty, disposable* database, for example the compose stack's
PostgreSQL bound to localhost:

    RMA_TEST_POSTGRES_URL=postgresql+psycopg://rma:secret@127.0.0.1:5432/rma_test \
        uv run pytest tests/integration/test_postgres_platform.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
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


# --- one poll cycle across processes ---------------------------------------------------------------


def test_advisory_lock_is_exclusive_released_on_exit_and_on_a_crashed_holder(pg_engine):
    from rma_portal.infrastructure.db.advisory_lock import (
        SYNC_CYCLE_LOCK_KEY,
        PostgresAdvisoryCycleLock,
    )

    lock = PostgresAdvisoryCycleLock(pg_engine)
    with lock.try_acquire() as first:
        assert first is True
        with PostgresAdvisoryCycleLock(pg_engine).try_acquire() as second:
            assert second is False  # another process would skip its turn
    with lock.try_acquire() as again:
        assert again is True  # released when the holder finished

    # A holder that dies without unlocking (its connection just drops) never wedges the queue.
    crashed = pg_engine.connect()
    assert crashed.execute(text("select pg_try_advisory_lock(:k)"), {"k": SYNC_CYCLE_LOCK_KEY}).scalar()
    with lock.try_acquire() as while_held:
        assert while_held is False
    crashed.invalidate()  # the process died: its database session is gone, not pooled
    crashed.close()
    with lock.try_acquire() as after_crash:
        assert after_crash is True


@pytest.mark.asyncio
async def test_two_workers_never_run_a_synchronization_cycle_at_the_same_time(pg_engine):
    import asyncio

    from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
    from rma_portal.application.workflow_sync import SyncWorkflows
    from rma_portal.infrastructure.db.advisory_lock import PostgresAdvisoryCycleLock
    from rma_portal.infrastructure.db.session import create_session_factory
    from rma_portal.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
    from rma_portal.infrastructure.portal.workflow_catalog import default_catalog
    from tests.unit.application.fakes import FakePortalReader, complete, row

    uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(pg_engine))
    catalog = default_catalog()
    WorkflowCatalogSync(uow_factory, catalog).sync()
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        for workflow in uow.workflows.list_for_account(account.id):
            uow.workflows.update_admin_config(workflow.id, enabled=workflow.key == "photos_pending")
        uow.commit()

    gate = asyncio.Event()

    class GatedReader(FakePortalReader):
        async def read_workflow(self, definition):
            await gate.wait()  # hold the cycle open inside the read
            return complete(definition.key, row("p1"))

    class Factory:
        def __init__(self, reader):
            self.reader = reader
            self.opened = 0

        def has_saved_session(self):
            return True

        def open(self):
            self.opened += 1
            return self.reader

    first_factory, second_factory = Factory(GatedReader()), Factory(GatedReader())
    worker_a = SyncWorkflows(first_factory, uow_factory, catalog, cycle_lock=PostgresAdvisoryCycleLock(pg_engine))
    worker_b = SyncWorkflows(second_factory, uow_factory, catalog, cycle_lock=PostgresAdvisoryCycleLock(pg_engine))

    running = asyncio.create_task(worker_a.execute())
    await asyncio.sleep(0.5)  # worker A is now inside its cycle, holding the advisory lock
    skipped = await worker_b.execute()

    assert skipped.skipped and "another" in skipped.skip_reason
    assert second_factory.opened == 0  # the second worker never even opened a browser
    gate.set()
    assert (await running).status.value == "COMPLETE"
    assert (await worker_b.execute()).skipped is False  # the lock was released


@pytest.mark.asyncio
async def test_a_full_synchronization_and_every_read_model_work_on_postgres(pg_engine):
    """Baseline, arrival, change and departure through SyncWorkflows, then the API read side."""
    from rma_portal.application.work_service import ItemQuery, WorkService
    from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
    from rma_portal.application.workflow_sync import SyncWorkflows
    from rma_portal.domain.enums import Role, WorkStatus
    from rma_portal.domain.models import User
    from rma_portal.infrastructure.db.session import create_session_factory
    from rma_portal.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
    from rma_portal.infrastructure.portal.workflow_catalog import default_catalog
    from tests.unit.application.fakes import FakePortalReaderFactory, complete, row

    uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(pg_engine))
    catalog = default_catalog()
    WorkflowCatalogSync(uow_factory, catalog).sync()
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        for workflow in uow.workflows.list_for_account(account.id):
            uow.workflows.update_admin_config(
                workflow.id, enabled=workflow.key in {"photos_pending", "agreement_validated"}
            )
        alice = uow.users.create(
            User(None, "alice", "Alice", "h", Role.EMPLOYEE, True, datetime.now(UTC))
        ).id
        uow.commit()

    photos, validated = "photos_pending", "agreement_validated"
    factory = FakePortalReaderFactory(
        [
            {photos: complete(photos, row("p1", date_envoi="01/09/2026")), validated: complete(validated)},
            {
                photos: complete(photos, row("p1", date_envoi="05/09/2026"), row("p2", insured_name="Sara")),
                validated: complete(validated, row("v1")),
            },
        ]
    )
    sync = SyncWorkflows(factory, uow_factory, catalog)
    first = await sync.execute()
    second = await sync.execute()

    assert first.notifications_created == 0
    assert (second.created, second.changed, second.notifications_created) == (2, 1, 2)
    service = WorkService(uow_factory, catalog, base_url="https://omegaflow.example")
    dashboard = service.dashboard(alice)
    assert dashboard.counters.actionable_new == 1 and dashboard.counters.changed_unread == 1
    assert {e.notification_class.value for e in dashboard.activity} == {"ACTION", "INFORMATIONAL"}
    page = service.items(alice, ItemQuery(search="sara"))
    assert [i.record_id for i in page.items] == ["p2"]
    everything = service.items(alice, ItemQuery(sort="date", page_size=10))
    assert everything.total == 3 and everything.facets.workflows == [validated, photos]
    dossier = service.dossier(alice, page.items[0].dossier_id)
    assert dossier.memberships[0].occurrences[0].unread is True
    assert service.acknowledge_occurrence(alice, page.items[0].occurrence_id) == 1
    state = service.set_work_status(alice, page.items[0].membership_id, WorkStatus.DONE, 1)
    assert (state.status, state.version) == (WorkStatus.DONE, 2)
    assert len(service.search("D-p")) == 2
    health = service.sync_health(alice)
    assert health.last_run.workflows_total == 2 and len(health.recent_runs) == 2
