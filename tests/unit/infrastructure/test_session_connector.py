"""SessionConnector: the in-app 'Se connecter'/'Reconnecter' workflow.

Pure asyncio tests (no web layer, no real Camoufox) -- ``open_and_wait``,
``verify_session`` and ``run_sync`` are injected as controllable fakes, and
the end-to-end scenarios reuse the same ``FakePortalReaderFactory``/
``SyncAgreementQueue`` test doubles already used for the scheduler/manual-
refresh tests, so this exercises the *real* authentication-detection path
rather than a second implementation.

Covers the three-phase design (connecting -> verifying -> fire-and-forget
sync) and the regressions called out for the "stuck on Connexion en cours"
bug: the login phase must end the instant the browser closes regardless of
how long verification/sync take afterward, a failed/timed-out verification
must leave a usable reconnect action instead of a stuck disabled button, and
shutdown/cancellation must never leave an unhandled future/task behind.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from filelock import FileLock, Timeout

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    BrowserTeardownError,
    PortalAuthRequiredError,
    QueueSnapshot,
)
from rma_portal.application.sync_service import SyncAgreementQueue
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from tests.unit.application.fakes import FakePortalReaderFactory
from tests.unit.application.test_sync_service import _row


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "rma-portal-data")


async def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> None:
    """Condition-based waiting -- polls instead of a single fixed sleep, so
    tests never race the extra async hops the verify/sync phases add."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(interval)


async def _wait_for_verification_to_finish(connector: SessionConnector) -> None:
    """Waits for verification to *start* first -- otherwise
    ``is_verifying is False`` is trivially true before the verify task even
    exists, and the wait returns before verification ran at all."""
    await _wait_until(lambda: connector._verify_task is not None)
    await _wait_until(lambda: connector.is_verifying is False)


class _ControllableOpenAndWait:
    """Simulates a visible browser that stays open until told to close."""

    def __init__(self) -> None:
        self.calls = 0
        self.close_event = asyncio.Event()

    async def __call__(self, settings: Settings) -> None:
        self.calls += 1
        await self.close_event.wait()


class _ControllableVerify:
    """Simulates ``SyncAgreementQueue.verify_session``: a bounded, controllable check."""

    def __init__(self, *, result: bool = True, hold: bool = False) -> None:
        self.calls: list[float] = []
        self.result = result
        self._release = asyncio.Event()
        if not hold:
            self._release.set()

    async def __call__(self, timeout_seconds: float) -> bool:
        self.calls.append(timeout_seconds)
        await self._release.wait()
        return self.result

    def release(self) -> None:
        self._release.set()


class _Counter:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> object:
        self.count += 1
        return None


def _connector(
    tmp_path, *, open_and_wait, verify_session=None, run_sync=None, **kwargs
) -> SessionConnector:
    return SessionConnector(
        _settings(tmp_path),
        verify_session=verify_session or _ControllableVerify(),
        run_sync=run_sync or _Counter(),
        open_and_wait=open_and_wait,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_start_launches_a_background_task_and_reports_active(tmp_path):
    fake = _ControllableOpenAndWait()
    connector = _connector(tmp_path, open_and_wait=fake)

    started = connector.start()
    await asyncio.sleep(0)

    assert started is True
    assert connector.is_active is True
    assert fake.calls == 1

    fake.close_event.set()
    await _wait_until(lambda: connector.is_active is False)


@pytest.mark.asyncio
async def test_duplicate_start_does_not_launch_a_second_browser(tmp_path):
    fake = _ControllableOpenAndWait()
    connector = _connector(tmp_path, open_and_wait=fake)

    first = connector.start()
    await asyncio.sleep(0)
    second = connector.start()
    await asyncio.sleep(0)

    assert first is True
    assert second is False
    assert fake.calls == 1

    fake.close_event.set()
    await _wait_until(lambda: connector.is_active is False)


@pytest.mark.asyncio
async def test_duplicate_start_is_rejected_while_verifying(tmp_path):
    """A second click after the browser has already closed, while the
    bounded auth check is still running, must not open a second browser."""
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(hold=True)
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: connector.is_verifying is True)

    second = connector.start()

    assert second is False
    assert fake.calls == 1
    verify.release()
    await _wait_until(lambda: connector.is_verifying is False)


@pytest.mark.asyncio
async def test_closing_the_browser_ends_connecting_immediately_even_if_verification_is_slow(
    tmp_path,
):
    """Regression: CONNECTING must never include verification/sync (the
    original bug -- is_active spanned the entire post-close sync)."""
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(hold=True)  # never resolves during this test
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify)

    connector.start()
    await asyncio.sleep(0)
    assert connector.is_active is True

    fake.close_event.set()
    await _wait_until(lambda: connector.is_active is False)

    # Login has ended even though verification is still (deliberately) stuck.
    assert connector.is_active is False
    await _wait_until(lambda: connector.is_verifying is True)
    verify.release()


@pytest.mark.asyncio
async def test_verifying_state_is_reported_while_verification_runs(tmp_path):
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(hold=True)
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: connector.is_verifying is True)

    assert connector.is_active is False
    assert connector.is_verifying is True

    verify.release()
    await _wait_until(lambda: connector.is_verifying is False)


@pytest.mark.asyncio
async def test_successful_verification_triggers_run_sync_exactly_once(tmp_path):
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(result=True)
    run_sync = _Counter()
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify, run_sync=run_sync)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: run_sync.count == 1)

    assert verify.calls == [connector._verify_timeout_seconds]


@pytest.mark.asyncio
async def test_failed_verification_does_not_trigger_run_sync(tmp_path):
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(result=False)
    run_sync = _Counter()
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify, run_sync=run_sync)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_for_verification_to_finish(connector)
    await asyncio.sleep(0.05)  # give a wrongly-triggered sync a chance to start

    assert run_sync.count == 0


@pytest.mark.asyncio
async def test_verification_timeout_restores_a_usable_reconnect_action(tmp_path):
    """Regression: a verification failure/timeout must leave CONNECTING (and
    VERIFYING) so the dashboard shows an actionable retry, not a stuck
    disabled button -- verify_session itself never raises (see
    SyncAgreementQueue.verify_session), it just returns False."""
    fake = _ControllableOpenAndWait()

    async def timed_out_verify(timeout_seconds: float) -> bool:
        raise TimeoutError

    connector = _connector(tmp_path, open_and_wait=fake, verify_session=timed_out_verify)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_for_verification_to_finish(connector)

    assert connector.is_active is False
    assert connector.is_verifying is False
    assert connector.start() is True  # a fresh click works again
    connector._login_task.cancel()  # avoid leaking a task past the test


@pytest.mark.asyncio
async def test_profile_lock_conflict_is_skipped_without_verifying(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_directories()
    lock = FileLock(str(settings.browser_lock_path), timeout=0)
    lock.acquire()
    try:
        fake = _ControllableOpenAndWait()
        verify = _ControllableVerify()
        connector = SessionConnector(
            settings, verify_session=verify, run_sync=_Counter(), open_and_wait=fake
        )

        connector.start()
        await _wait_until(lambda: connector.is_active is False)

        assert fake.calls == 0  # never even attempted to open a browser
        assert connector.is_verifying is False
        assert verify.calls == []
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_shutdown_cancels_an_open_login_window_without_verifying(tmp_path):
    fake = _ControllableOpenAndWait()  # never closes on its own
    verify = _ControllableVerify()
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify)

    connector.start()
    await asyncio.sleep(0.02)
    assert connector.is_active is True

    await connector.shutdown()

    assert connector.is_active is False
    assert verify.calls == []


@pytest.mark.asyncio
async def test_shutdown_cancels_a_running_verification(tmp_path):
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(hold=True)
    run_sync = _Counter()
    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify, run_sync=run_sync)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: connector.is_verifying is True)

    await connector.shutdown()

    assert connector.is_verifying is False
    assert run_sync.count == 0


@pytest.mark.asyncio
async def test_shutdown_awaits_a_running_sync_without_unhandled_task_warnings(tmp_path, caplog):
    """Regression: cleanup/cancellation must release the lock without
    leaving an unhandled future/task behind (the "unhandled CancelledError/
    Future warning during shutdown" observed live)."""
    fake = _ControllableOpenAndWait()
    verify = _ControllableVerify(result=True)
    sync_gate = asyncio.Event()

    async def slow_sync() -> None:
        await sync_gate.wait()

    connector = _connector(tmp_path, open_and_wait=fake, verify_session=verify, run_sync=slow_sync)

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: connector._sync_task is not None)

    with caplog.at_level(logging.WARNING, logger="asyncio"):
        await connector.shutdown()

    assert "was never retrieved" not in caplog.text
    assert "was destroyed but it is pending" not in caplog.text


@pytest.mark.asyncio
async def test_profile_lock_is_released_before_verification_runs(tmp_path):
    """Regression for 'verify immediately after the login browser closes':
    the profile lock must already be free when verification runs, since the
    real callback (SyncAgreementQueue.verify_session) re-acquires that same
    lock through its own reader."""
    settings = _settings(tmp_path)
    fake = _ControllableOpenAndWait()
    observed: list[bool] = []

    async def verify_session(timeout_seconds: float) -> bool:
        lock = FileLock(str(settings.browser_lock_path), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            observed.append(False)
        else:
            observed.append(True)
            lock.release()
        return True

    connector = SessionConnector(
        settings, verify_session=verify_session, run_sync=_Counter(), open_and_wait=fake
    )
    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_until(lambda: observed != [])

    assert observed == [True]


@pytest.mark.asyncio
async def test_closing_without_login_results_in_auth_required(tmp_path, uow_factory, portal_account_id):
    reader_factory = FakePortalReaderFactory(
        polls=[PortalAuthRequiredError("toujours sur la page de connexion")]
    )
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(
        _settings(tmp_path),
        verify_session=sync_service.verify_session,
        run_sync=sync_service.execute,
        open_and_wait=fake,
    )

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()  # employee closed the window without logging in
    await _wait_for_verification_to_finish(connector)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status.value == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_successful_login_results_in_ready_before_the_full_sync_finishes(
    tmp_path, uow_factory, portal_account_id
):
    """Regression: READY must appear as soon as the bounded check succeeds,
    not after the full dossier baseline/enrichment -- proven here by holding
    the fire-and-forget sync itself back with a gate the test controls."""
    row = _row("a")
    sync_gate = asyncio.Event()
    reader_factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(), pages_seen=1),  # verify: authenticated
            QueueSnapshot(rows=(row,), pages_seen=1),  # full sync: baseline
        ]
    )
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)

    async def gated_run_sync():
        await sync_gate.wait()
        return await sync_service.execute()

    fake = _ControllableOpenAndWait()
    connector = SessionConnector(
        _settings(tmp_path),
        verify_session=sync_service.verify_session,
        run_sync=gated_run_sync,
        open_and_wait=fake,
    )

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_for_verification_to_finish(connector)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert account.session_status.value == "READY"
    # The full sync has not even started yet -- still held by the gate.
    assert state == {}
    assert sync_service.is_running is False

    def _dossier_created() -> bool:
        with uow_factory() as uow:
            return bool(uow.dossiers.existing_state_by_account(portal_account_id))

    sync_gate.set()
    await _wait_until(_dossier_created)


@pytest.mark.asyncio
async def test_later_scheduled_poll_flips_ready_to_auth_required_and_preserves_dossiers(
    tmp_path, uow_factory, portal_account_id
):
    row = _row("a")
    reader_factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(), pages_seen=1),  # the connect-triggered bounded check
            QueueSnapshot(rows=(row,), pages_seen=1),  # the connect-triggered full sync: baseline
            PortalAuthRequiredError("session expirée"),  # a later scheduled poll
        ]
    )
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    fake = _ControllableOpenAndWait()
    connector = SessionConnector(
        _settings(tmp_path),
        verify_session=sync_service.verify_session,
        run_sync=sync_service.execute,
        open_and_wait=fake,
    )

    connector.start()
    await asyncio.sleep(0)
    fake.close_event.set()
    await _wait_for_verification_to_finish(connector)
    await _wait_until(lambda: sync_service.is_running is False)

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert account.session_status.value == "READY"
    assert state["a"].active is True

    # A later scheduled poll (same SyncAgreementQueue the poller uses).
    result = await sync_service.execute()

    assert result.status.value == "AUTH_REQUIRED"
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert account.session_status.value == "AUTH_REQUIRED"
    assert state["a"].active is True
    assert state["a"].missing_complete_polls == 0


@pytest.mark.asyncio
async def test_login_teardown_failure_ends_connecting_and_marks_the_profile_unavailable(tmp_path):
    """Regression: a stalled visible-login browser teardown (here
    signalled by launch_visible_browser_and_wait raising
    BrowserTeardownError, exactly as it does when its own bounded
    AsyncCamoufox close does not finish) must not leave CONNECTING stuck,
    must never start verification on the strength of a browser whose
    teardown was never confirmed, and must leave the profile unavailable
    (not silently release the lock) until the application restarts."""
    settings = _settings(tmp_path)

    async def open_and_wait_then_fail_teardown(_settings: Settings) -> None:
        raise BrowserTeardownError("teardown stalled (test)")

    verify = _ControllableVerify()
    connector = _connector(
        tmp_path, open_and_wait=open_and_wait_then_fail_teardown, verify_session=verify
    )

    connector.start()
    await _wait_until(lambda: connector.is_active is False)

    assert connector.is_verifying is False
    assert verify.calls == []  # never started verification

    with (
        pytest.raises(BrowserProfileLockedError, match="indisponible"),
        acquire_profile_lock(settings.browser_lock_path),
    ):
        pass
