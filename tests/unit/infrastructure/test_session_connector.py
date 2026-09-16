"""SessionConnector: the in-app 'Se connecter'/'Reconnecter' workflow.

Pure asyncio tests (no web layer, no real Camoufox) -- ``open_and_wait`` is
injected as a controllable fake, and the post-close sync scenarios reuse
the same ``FakePortalReaderFactory``/``SyncAgreementQueue`` test doubles
already used for the scheduler/manual-refresh tests, so this exercises the
*real* authentication-detection path rather than a second implementation.
"""

from __future__ import annotations

import asyncio

import pytest
from filelock import FileLock, Timeout

from rma_portal.application.dto import PortalAuthRequiredError, QueueSnapshot
from rma_portal.application.sync_service import SyncAgreementQueue
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from tests.unit.application.fakes import FakePortalReaderFactory
from tests.unit.application.test_sync_service import _row


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "rma-portal-data")


class _ControllableOpenAndWait:
    """Simulates a visible browser that stays open until told to close."""

    def __init__(self) -> None:
        self.calls = 0
        self.close_event = asyncio.Event()

    async def __call__(self, settings: Settings) -> None:
        self.calls += 1
        await self.close_event.wait()


class _Counter:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


@pytest.mark.asyncio
async def test_start_launches_a_background_task_and_reports_active(tmp_path):
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(_settings(tmp_path), on_closed=_Counter(), open_and_wait=fake)

    started = connector.start()
    await asyncio.sleep(0)

    assert started is True
    assert connector.is_active is True
    assert fake.calls == 1

    fake.close_event.set()
    await asyncio.sleep(0.05)
    assert connector.is_active is False


@pytest.mark.asyncio
async def test_duplicate_start_does_not_launch_a_second_browser(tmp_path):
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(_settings(tmp_path), on_closed=_Counter(), open_and_wait=fake)

    first = connector.start()
    await asyncio.sleep(0)
    second = connector.start()
    await asyncio.sleep(0)

    assert first is True
    assert second is False
    assert fake.calls == 1

    fake.close_event.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_on_closed_runs_once_after_the_window_closes(tmp_path):
    fake = _ControllableOpenAndWait()
    on_closed = _Counter()
    connector = SessionConnector(_settings(tmp_path), on_closed=on_closed, open_and_wait=fake)

    connector.start()
    await asyncio.sleep(0.02)
    assert on_closed.count == 0  # still open: must not have run yet

    fake.close_event.set()
    await asyncio.sleep(0.05)
    assert on_closed.count == 1
    assert connector.is_active is False


@pytest.mark.asyncio
async def test_profile_lock_conflict_is_skipped_without_calling_on_closed(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_directories()
    lock = FileLock(str(settings.browser_lock_path), timeout=0)
    lock.acquire()
    try:
        fake = _ControllableOpenAndWait()
        on_closed = _Counter()
        connector = SessionConnector(settings, on_closed=on_closed, open_and_wait=fake)

        connector.start()
        await asyncio.sleep(0.05)

        assert connector.is_active is False  # finished (skipped), not left hanging
        assert fake.calls == 0  # never even attempted to open a browser
        assert on_closed.count == 0
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_shutdown_cancels_an_open_window_without_calling_on_closed(tmp_path):
    fake = _ControllableOpenAndWait()  # never closes on its own
    on_closed = _Counter()
    connector = SessionConnector(_settings(tmp_path), on_closed=on_closed, open_and_wait=fake)

    connector.start()
    await asyncio.sleep(0.02)
    assert connector.is_active is True

    await connector.shutdown()

    assert connector.is_active is False
    assert on_closed.count == 0


@pytest.mark.asyncio
async def test_profile_lock_is_released_before_on_closed_runs(tmp_path):
    """Regression for 'verify immediately after the login browser closes':
    the profile lock must already be free when the callback runs, since the
    real callback (SyncAgreementQueue.execute) re-acquires that same lock
    through its own reader."""
    settings = _settings(tmp_path)
    fake = _ControllableOpenAndWait()
    observed: list[bool] = []

    async def on_closed() -> None:
        lock = FileLock(str(settings.browser_lock_path), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            observed.append(False)
        else:
            observed.append(True)
            lock.release()

    connector = SessionConnector(settings, on_closed=on_closed, open_and_wait=fake)
    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await asyncio.sleep(0.05)

    assert observed == [True]


@pytest.mark.asyncio
async def test_closing_without_login_results_in_auth_required(tmp_path, uow_factory, portal_account_id):
    reader_factory = FakePortalReaderFactory(
        polls=[PortalAuthRequiredError("toujours sur la page de connexion")]
    )
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(_settings(tmp_path), on_closed=sync_service.execute, open_and_wait=fake)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()  # employee closed the window without logging in
    await asyncio.sleep(0.05)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status.value == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_successful_login_results_in_ready(tmp_path, uow_factory, portal_account_id):
    reader_factory = FakePortalReaderFactory(polls=[QueueSnapshot(rows=(), pages_seen=1)])
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(_settings(tmp_path), on_closed=sync_service.execute, open_and_wait=fake)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await asyncio.sleep(0.05)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status.value == "READY"


@pytest.mark.asyncio
async def test_later_scheduled_poll_flips_ready_to_auth_required_and_preserves_dossiers(
    tmp_path, uow_factory, portal_account_id
):
    row = _row("a")
    reader_factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(row,), pages_seen=1),  # the connect-triggered check: baseline
            PortalAuthRequiredError("session expirée"),  # a later scheduled poll
        ]
    )
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(_settings(tmp_path), on_closed=sync_service.execute, open_and_wait=fake)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await asyncio.sleep(0.05)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status.value == "READY"

    # A later scheduled poll (same SyncAgreementQueue the poller uses).
    result = await sync_service.execute()

    assert result.status.value == "AUTH_REQUIRED"
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert account.session_status.value == "AUTH_REQUIRED"
    assert state["a"].active is True
    assert state["a"].missing_complete_polls == 0
