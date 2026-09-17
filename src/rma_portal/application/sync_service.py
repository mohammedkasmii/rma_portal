"""SyncAgreementQueue: the orchestration use case for one OmegaFlow poll.

This module wires I/O (the portal reader, the database) to the pure policy
in ``domain.sync_rules``. It never talks to SQLAlchemy or Camoufox directly
-- only through the ``application.ports`` protocols -- so it can be tested
with in-memory fakes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    DetailReadError,
    DossierDetails,
    PortalAuthRequiredError,
    PortalDossierRef,
    PortalPartialReadError,
    PortalReadError,
    QueueSnapshot,
    SyncResult,
)
from rma_portal.application.ports import PortalReader, PortalReaderFactory, UnitOfWorkFactory
from rma_portal.domain.enums import NotificationKind, PollStatus, SessionStatus
from rma_portal.domain.models import Dossier, DossierDates
from rma_portal.domain.sync_rules import ExistingDossierState, ReconciliationInput, reconcile
from rma_portal.observability import log_stage, new_operation_id, operation_context

logger = logging.getLogger(__name__)

_CLEANUP_TIMEOUT_SECONDS = 30.0

_UNCONFIRMED_CLEANUP_MESSAGE = (
    "Le nettoyage du navigateur après synchronisation a échoué ou a expiré ; le profil "
    "reste indisponible jusqu'au redémarrage du Portail RMA."
)


class SyncAgreementQueue:
    """Polls the 'Garage agréé' queue for the default portal account.

    A single instance is shared between the 5-minute scheduler and the
    manual refresh endpoint, guaranteeing "only one sync may run at a time"
    and that a concurrent second caller receives the in-flight result
    instead of starting a duplicate poll.
    """

    def __init__(
        self,
        reader_factory: PortalReaderFactory,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        self._reader_factory = reader_factory
        self._uow_factory = uow_factory
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Future[SyncResult] | None = None

    @property
    def is_running(self) -> bool:
        """True while a poll (scheduled, manual, or connect-triggered) is in flight."""
        return self._lock.locked()

    async def execute(self) -> SyncResult:
        if self._lock.locked() and self._inflight is not None:
            return await asyncio.shield(self._inflight)

        loop = asyncio.get_running_loop()
        inflight: asyncio.Future[SyncResult] = loop.create_future()
        self._inflight = inflight
        async with self._lock:
            # Every actual execution (never the shield-only coalesce path
            # above) gets its own fresh correlation ID -- "each ...
            # synchronization gets a correlation ID", including one
            # triggered right after a login (SessionConnector), which
            # already has its own "conn-*" ID in context; this
            # deliberately overrides it rather than inheriting it, since
            # the sync is its own operation with its own poll_runs row.
            with operation_context(new_operation_id("sync")):
                try:
                    result = await self._execute_once()
                except BaseException as exc:  # noqa: BLE001 - propagate after publishing
                    if not inflight.done():
                        inflight.set_exception(exc)
                    # A concurrent caller may never materialize to shield/await
                    # this Future (e.g. a solo cancellation during shutdown).
                    # asyncio logs "Future exception was never retrieved" at GC
                    # time unless *someone* reads it -- reading it here (harmless;
                    # multiple reads of a resolved Future are fine) guarantees
                    # that always happens, while any concurrent shielder still
                    # observes the same exception independently.
                    inflight.exception()
                    self._inflight = None
                    raise
                if not inflight.done():
                    inflight.set_result(result)
                self._inflight = None
                return result

    async def _execute_once(self) -> SyncResult:
        now = datetime.now(UTC)
        sync_started = time.perf_counter()
        logger.info("stage=synchronization outcome=START")

        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                raise RuntimeError("no portal account is configured")
            if not account.enabled:
                logger.info(
                    "stage=synchronization outcome=SKIPPED elapsed_ms=%.1f reason=account_disabled",
                    (time.perf_counter() - sync_started) * 1000,
                )
                return SyncResult(status=None, skipped=True, skip_reason="portal account disabled")
            account_id = account.id
            baseline_already_completed = account.baseline_completed_at is not None

        # The browser/profile is opened *before* any poll_runs row is
        # created: a BrowserProfileLockedError is a safe skip that must
        # leave no record at all, and any other launch failure gets exactly
        # one row, created and finished together as FAILED -- never a
        # dangling "started but not finished" record.
        try:
            reader_cm = self._reader_factory.open()
        except BrowserProfileLockedError as exc:
            logger.info(
                "stage=synchronization outcome=SKIPPED elapsed_ms=%.1f reason=profile_locked",
                (time.perf_counter() - sync_started) * 1000,
            )
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))

        try:
            reader = await reader_cm.__aenter__()
        except BrowserProfileLockedError as exc:
            logger.info(
                "stage=synchronization outcome=SKIPPED elapsed_ms=%.1f reason=profile_locked",
                (time.perf_counter() - sync_started) * 1000,
            )
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - any launch/context failure
            return self._record_failed_poll(account_id, now, sync_started, _short_error(exc))

        try:
            with self._uow_factory() as uow:
                uow.portal_accounts.mark_poll_started(account_id, now)
                poll_run = uow.poll_runs.start(account_id, now)
                poll_run_id = poll_run.id
                uow.commit()
        except Exception as exc:  # noqa: BLE001 - cannot even record the attempt
            if not await self._close_reader(reader_cm):
                return self._record_failed_poll(
                    account_id, now, sync_started, _UNCONFIRMED_CLEANUP_MESSAGE
                )
            return self._record_failed_poll(account_id, now, sync_started, _short_error(exc))

        try:
            snapshot = QueueSnapshot()
            status: PollStatus
            error: str | None = None

            try:
                snapshot = await reader.read_agreement_queue()
                status = PollStatus.COMPLETE
            except PortalAuthRequiredError as exc:
                status, error = PollStatus.AUTH_REQUIRED, str(exc)
            except PortalPartialReadError as exc:
                snapshot, status, error = exc.partial, PollStatus.PARTIAL, str(exc)
            except PortalReadError as exc:
                status, error = PollStatus.FAILED, str(exc)

            created = reactivated = deactivated = notified = details_attempted = details_failed = 0
            detail_read_seconds = 0.0
            if status in (PollStatus.COMPLETE, PollStatus.PARTIAL):
                (
                    created,
                    reactivated,
                    deactivated,
                    notified,
                    details_attempted,
                    details_failed,
                    detail_read_seconds,
                ) = await self._reconcile_and_enrich(
                    reader, account_id, status, baseline_already_completed, snapshot, now
                )
        except Exception as exc:  # noqa: BLE001 - unexpected mid-poll failure
            status = PollStatus.FAILED
            error = _short_error(exc)
            logger.error("stage=synchronization outcome=FAILED unexpected mid-poll error", exc_info=True)
            snapshot = QueueSnapshot()
            created = reactivated = deactivated = notified = details_attempted = details_failed = 0
            detail_read_seconds = 0.0
        finally:
            cleanup_ok = await self._close_reader(reader_cm)

        if not cleanup_ok:
            # Overrides whatever the read/reconcile stage found -- even a
            # COMPLETE poll -- the same rule verify_session follows: an
            # unconfirmed teardown means this browser session can no
            # longer be accounted for, so the poll itself must finish as
            # FAILED/ERROR. Whatever was already reconciled and committed
            # by _reconcile_and_enrich is deliberately left untouched
            # (created/reactivated/deactivated/notified/details_failed and
            # the snapshot counts are not reset here) -- only the
            # *cleanup* failed, not the data captured before it.
            status = PollStatus.FAILED
            error = _UNCONFIRMED_CLEANUP_MESSAGE

        completed_at = datetime.now(UTC)
        with self._uow_factory() as uow:
            uow.poll_runs.finish(
                poll_run_id,
                status=status,
                completed_at=completed_at,
                rows_seen=snapshot.rows_seen,
                pages_seen=snapshot.pages_seen,
                details_failed=details_failed,
                error=error,
            )
            uow.portal_accounts.mark_poll_finished(
                account_id, status=status, polled_at=completed_at, error=error
            )
            uow.commit()

        total_elapsed_ms = (time.perf_counter() - sync_started) * 1000
        details_ok = details_attempted - details_failed
        summary_log = logger.info if status == PollStatus.COMPLETE else logger.warning
        summary_log(
            "stage=synchronization outcome=%s elapsed_ms=%.1f pages=%d rows=%d "
            "detail_attempts=%d details_ok=%d details_failed=%d detail_read_ms=%.1f",
            status,
            total_elapsed_ms,
            snapshot.pages_seen,
            snapshot.rows_seen,
            details_attempted,
            details_ok,
            details_failed,
            detail_read_seconds * 1000,
        )

        return SyncResult(
            status=status,
            rows_seen=snapshot.rows_seen,
            pages_seen=snapshot.pages_seen,
            details_failed=details_failed,
            error=error,
            created=created,
            reactivated=reactivated,
            deactivated=deactivated,
            notifications_created=notified,
        )

    async def _close_reader(self, reader_cm) -> bool:
        """Bounded, best-effort close -- returns whether it was confirmed
        to finish within :data:`_CLEANUP_TIMEOUT_SECONDS`.

        Mirrors :meth:`verify_session`'s own bounded cleanup: a plain,
        unbounded ``await reader_cm.__aexit__(...)`` here would let a
        stalled browser-manager teardown keep this poll (and therefore
        ``is_running``) pending indefinitely, whether reached via a
        scheduled poll, a manual refresh, or the post-login synchronization
        ``SessionConnector`` triggers -- all three share this one
        ``execute()``/``_execute_once()`` implementation. Never manages the
        profile lock itself: ``CamoufoxPortalReader.__aexit__`` already
        keeps it held (never releases it) whenever its own teardown cannot
        be confirmed, exactly the same protection ``verify_session`` and
        the visible login browser rely on -- this only reports the outcome
        so the poll itself can be finished correctly (FAILED/ERROR, never
        a false success) instead of also managing lock ownership here.
        """
        started = time.perf_counter()
        try:
            await asyncio.wait_for(
                reader_cm.__aexit__(None, None, None), timeout=_CLEANUP_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - reported via the return value, not swallowed
            logger.exception(
                "échec du nettoyage du navigateur après synchronisation "
                "elapsed_ms=%.1f timeout_s=%.0f",
                (time.perf_counter() - started) * 1000,
                _CLEANUP_TIMEOUT_SECONDS,
            )
            return False
        return True

    async def verify_session(self, timeout_seconds: float) -> bool:
        """Short, bounded, read-only check that the saved profile is still
        authenticated -- deliberately does *not* read the queue or enrich
        any dossier, so it returns long before a full baseline/enrichment
        poll would. Used right after the employee closes the manual login
        window, so the dashboard can show READY without waiting for
        :meth:`execute` to run the normal synchronization afterward.

        Reuses the same reader (and therefore the same
        ``_assert_authenticated``/``detect_auth_required`` logic, plus the
        positive-evidence check in ``verify_authenticated`` itself) that
        :meth:`execute` uses -- there is exactly one implementation of
        "is OmegaFlow authenticated". Never raises for an ordinary
        failure: the boolean return is the only signal callers need, and
        the account's persisted ``session_status`` is always updated to
        match (READY, AUTH_REQUIRED, or ERROR on any other failure/
        timeout) so the dashboard immediately reflects an actionable state
        either way.

        Browser startup, the auth check itself, and cleanup are each
        bounded by their own independent ``timeout_seconds`` wait --
        deliberately three separate ``asyncio.wait_for`` calls rather than
        one wrapping all three. A single wrapper let a stall *inside*
        cleanup (reached via a ``finally: await ...`` after the wrapped
        coroutine had already been cancelled once) run unbounded: Python
        only delivers a cancellation at the *current* await point, so a
        fresh ``await`` started while handling that cancellation is not
        itself re-cancelled by the same timeout. Cleanup is still always
        awaited (bounded) rather than abandoned.

        A cleanup failure/timeout always wins over whatever the auth check
        found: this method never reports READY (or persists any other
        outcome the check determined) for a browser session it can no
        longer account for. ``CamoufoxPortalReader.__aexit__`` itself
        never releases the profile lock when its own teardown did not
        complete, so the profile is unavailable (a fast, actionable
        ``BrowserProfileLockedError`` on the next attempt) until the
        application restarts -- a fresh connect attempt can never race a
        browser process that might still be running against it.
        """
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                raise RuntimeError("no portal account is configured")
            account_id = account.id

        try:
            reader_cm = self._reader_factory.open()
        except BrowserProfileLockedError as exc:
            self._mark_session_checked(account_id, SessionStatus.ERROR, now, str(exc))
            return False

        reader: PortalReader | None = None
        cleanup_failed = False
        try:
            try:
                reader = await asyncio.wait_for(reader_cm.__aenter__(), timeout=timeout_seconds)
            except BrowserProfileLockedError as exc:
                status, error = SessionStatus.ERROR, str(exc)
            except TimeoutError:
                status, error = (
                    SessionStatus.ERROR,
                    f"Démarrage du navigateur interrompu après {timeout_seconds:.0f}s.",
                )
            except Exception as exc:  # noqa: BLE001 - any other launch failure
                status, error = SessionStatus.ERROR, _short_error(exc)
            else:
                assert reader is not None  # this branch only runs when __aenter__ succeeded
                try:
                    await asyncio.wait_for(reader.verify_authenticated(), timeout=timeout_seconds)
                except PortalAuthRequiredError as exc:
                    status, error = SessionStatus.AUTH_REQUIRED, str(exc)
                except TimeoutError:
                    status, error = (
                        SessionStatus.ERROR,
                        f"Vérification de session interrompue après {timeout_seconds:.0f}s.",
                    )
                except Exception as exc:  # noqa: BLE001 - any other read failure
                    status, error = SessionStatus.ERROR, _short_error(exc)
                else:
                    status, error = SessionStatus.READY, None
        finally:
            # Its own bound, independent of the stages above (see the
            # docstring) -- never skipped just because startup/the auth
            # check failed, timed out, or the whole method is itself being
            # cancelled (e.g. app shutdown): Python still runs `finally`,
            # and this `await` is a fresh one Python does not re-cancel on
            # its own, so it gets a genuine chance to finish instead of
            # being silently abandoned mid-teardown.
            if reader is not None:
                try:
                    await asyncio.wait_for(
                        reader_cm.__aexit__(None, None, None), timeout=timeout_seconds
                    )
                except Exception:  # noqa: BLE001 - never let cleanup crash the check
                    cleanup_failed = True
                    logger.exception(
                        "échec du nettoyage du navigateur après vérification de session"
                    )

        if cleanup_failed:
            # Overrides whatever the auth check found (even a successful
            # one): an unconfirmed teardown means this browser/profile can
            # no longer be accounted for, so nothing it reported can be
            # trusted as a basis for READY. CamoufoxPortalReader.__aexit__
            # has already kept the profile lock held (see its own
            # docstring/mark_profile_teardown_unconfirmed) -- this is only
            # the persisted, user-facing side of the same fact.
            status, error = (
                SessionStatus.ERROR,
                "Le nettoyage du navigateur après vérification de session a échoué ou "
                "a expiré ; le profil reste indisponible jusqu'au redémarrage du "
                "Portail RMA.",
            )

        self._mark_session_checked(account_id, status, now, error)
        return status is SessionStatus.READY

    async def mark_login_teardown_failed(self, message: str) -> None:
        """Persist ERROR with ``message`` for a *visible login browser*
        teardown that could not be confirmed (``SessionConnector`` calls
        this after catching ``BrowserTeardownError`` from
        ``launch_visible_browser_and_wait``).

        Reuses the exact same account-status persistence
        :meth:`verify_session` uses -- there is exactly one place that
        writes ``session_status`` -- so ``build_session_view`` reflects
        the failure immediately instead of falling back to whatever
        READY/UNKNOWN state happened to be persisted from before this
        connect attempt.
        """
        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                return
            account_id = account.id
        self._mark_session_checked(account_id, SessionStatus.ERROR, datetime.now(UTC), message)

    def _mark_session_checked(
        self, account_id: int, status: SessionStatus, checked_at: datetime, error: str | None
    ) -> None:
        with self._uow_factory() as uow:
            uow.portal_accounts.mark_session_checked(
                account_id, status=status, checked_at=checked_at, error=error
            )
            uow.commit()

    def _record_failed_poll(
        self, account_id: int, started_at: datetime, perf_started: float, error: str
    ) -> SyncResult:
        """A poll that never got a reader at all: one FAILED row, created
        and finished in the same transaction so nothing is ever left
        unfinished."""
        completed_at = datetime.now(UTC)
        with self._uow_factory() as uow:
            uow.portal_accounts.mark_poll_started(account_id, started_at)
            poll_run = uow.poll_runs.start(account_id, started_at)
            uow.poll_runs.finish(
                poll_run.id,
                status=PollStatus.FAILED,
                completed_at=completed_at,
                rows_seen=0,
                pages_seen=0,
                details_failed=0,
                error=error,
            )
            uow.portal_accounts.mark_poll_finished(
                account_id, status=PollStatus.FAILED, polled_at=completed_at, error=error
            )
            uow.commit()
        logger.warning(
            "stage=synchronization outcome=FAILED elapsed_ms=%.1f pages=0 rows=0 "
            "detail_attempts=0 details_ok=0 details_failed=0 detail_read_ms=0.0",
            (time.perf_counter() - perf_started) * 1000,
        )
        return SyncResult(status=PollStatus.FAILED, error=error)

    async def _reconcile_and_enrich(
        self,
        reader: PortalReader,
        account_id: int,
        status: PollStatus,
        baseline_already_completed: bool,
        snapshot: QueueSnapshot,
        now: datetime,
    ) -> tuple[int, int, int, int, int, int, float]:
        rows_by_id = {row.record_id: row for row in snapshot.rows}

        with self._uow_factory() as uow, log_stage(logger, "db_reconciliation", rows_seen=len(rows_by_id)):
            existing = uow.dossiers.existing_state_by_account(account_id)
            result = reconcile(
                ReconciliationInput(
                    poll_status=status,
                    baseline_already_completed=baseline_already_completed,
                    seen_record_ids=frozenset(rows_by_id),
                    existing={
                        rid: ExistingDossierState(rid, s.active, s.missing_complete_polls)
                        for rid, s in existing.items()
                    },
                )
            )

            # Queried before any mutation below: a dossier created or
            # reactivated by *this* poll always has detail_complete=False
            # and must not also be picked up here, or its detail page would
            # be fetched twice and double-count `details_failed`. Keyed by
            # dossier id so a dossier can never end up queued twice (e.g. a
            # touched dossier whose status changed but whose previous detail
            # fetch had also failed).
            dossiers_needing_detail: dict[int, Dossier] = {
                d.id: d for d in uow.dossiers.dossiers_needing_detail_retry(account_id)
            }
            notified = 0

            for record_id in result.to_create:
                dossier = uow.dossiers.create_from_row(account_id, rows_by_id[record_id], now)
                dossiers_needing_detail[dossier.id] = dossier
                if result.create_notifications:
                    uow.notifications.create(dossier.id, NotificationKind.NEW_AGREEMENT_DOSSIER, now)
                    notified += 1

            for record_id in result.to_reactivate:
                dossier = uow.dossiers.reactivate(account_id, rows_by_id[record_id], now)
                dossiers_needing_detail[dossier.id] = dossier
                if result.create_notifications:
                    uow.notifications.create(dossier.id, NotificationKind.NEW_AGREEMENT_DOSSIER, now)
                    notified += 1

            for record_id in result.to_touch:
                dossier, status_changed = uow.dossiers.touch(account_id, rows_by_id[record_id], now)
                if status_changed or _missing_required_quote_date(dossier):
                    dossiers_needing_detail[dossier.id] = dossier

            for record_id, count in result.absence_increments.items():
                uow.dossiers.apply_absence_increment(account_id, record_id, count)

            for record_id in result.to_deactivate:
                uow.dossiers.deactivate(account_id, record_id)

            if result.is_baseline_poll:
                uow.portal_accounts.mark_baseline_completed(account_id, now)

            uow.commit()

        details_failed = 0
        details_total = len(dossiers_needing_detail)
        detail_read_seconds = 0.0
        for index, dossier in enumerate(dossiers_needing_detail.values(), start=1):
            ref = _as_portal_ref(dossier)
            detail_started = time.perf_counter()
            try:
                with log_stage(logger, "dossier_detail_read", index=index, total=details_total):
                    details = await reader.read_dossier_details(ref)
            except DetailReadError as exc:
                detail_read_seconds += time.perf_counter() - detail_started
                details_failed += 1
                with log_stage(logger, "db_save", index=index), self._uow_factory() as uow:
                    uow.dossiers.save_details(dossier.id, _failed_details(str(exc)))
                    uow.commit()
                continue
            detail_read_seconds += time.perf_counter() - detail_started
            with log_stage(logger, "db_save", index=index), self._uow_factory() as uow:
                uow.dossiers.save_details(dossier.id, details)
                uow.commit()

        return (
            len(result.to_create),
            len(result.to_reactivate),
            len(result.to_deactivate),
            notified,
            details_total,
            details_failed,
            detail_read_seconds,
        )


def _as_portal_ref(dossier: Dossier) -> PortalDossierRef:
    return PortalDossierRef(record_id=dossier.record_id, details_href=dossier.details_href)


def _missing_required_quote_date(dossier: Dossier) -> bool:
    """True while 'Date envoi devis garage' has never been captured.

    This is V1's one *required* detail field (see docs/architecture.md), so
    an active dossier is refetched on every poll until it appears -- even
    when detail_complete is already True and portal_status is unchanged,
    since a prior successful read can legitimately have found it still
    blank at the garage. The other four detail dates are optional and do
    NOT get this treatment: retrying every dossier forever merely because
    an optional date is blank would be pure waste, so they only refresh
    when portal_status changes (see the `to_touch` loop) or via the normal
    detail_complete=False retry.
    """
    return dossier.dates.date_envoi_devis_garage is None


def _failed_details(error: str) -> DossierDetails:
    return DossierDetails(dates=DossierDates(), detail_complete=False, detail_error=error)


_MAX_ERROR_LENGTH = 500


def _short_error(exc: Exception) -> str:
    """A short technical message safe to store in poll_runs/last_error."""
    message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return message[:_MAX_ERROR_LENGTH]
