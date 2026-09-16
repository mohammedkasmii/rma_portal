"""SyncAgreementQueue: the orchestration use case for one OmegaFlow poll.

This module wires I/O (the portal reader, the database) to the pure policy
in ``domain.sync_rules``. It never talks to SQLAlchemy or Camoufox directly
-- only through the ``application.ports`` protocols -- so it can be tested
with in-memory fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
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

logger = logging.getLogger(__name__)


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

        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                raise RuntimeError("no portal account is configured")
            if not account.enabled:
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
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))

        try:
            reader = await reader_cm.__aenter__()
        except BrowserProfileLockedError as exc:
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - any launch/context failure
            return self._record_failed_poll(account_id, now, _short_error(exc))

        try:
            with self._uow_factory() as uow:
                uow.portal_accounts.mark_poll_started(account_id, now)
                poll_run = uow.poll_runs.start(account_id, now)
                poll_run_id = poll_run.id
                uow.commit()
        except Exception as exc:  # noqa: BLE001 - cannot even record the attempt
            with contextlib.suppress(Exception):
                await reader_cm.__aexit__(None, None, None)
            return self._record_failed_poll(account_id, now, _short_error(exc))

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

            created = reactivated = deactivated = notified = details_failed = 0
            if status in (PollStatus.COMPLETE, PollStatus.PARTIAL):
                created, reactivated, deactivated, notified, details_failed = (
                    await self._reconcile_and_enrich(
                        reader, account_id, status, baseline_already_completed, snapshot, now
                    )
                )
        except Exception as exc:  # noqa: BLE001 - unexpected mid-poll failure
            status = PollStatus.FAILED
            error = _short_error(exc)
            snapshot = QueueSnapshot()
            created = reactivated = deactivated = notified = details_failed = 0
        finally:
            with contextlib.suppress(Exception):
                await reader_cm.__aexit__(None, None, None)

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

    async def verify_session(self, timeout_seconds: float) -> bool:
        """Short, bounded, read-only check that the saved profile is still
        authenticated -- deliberately does *not* read the queue or enrich
        any dossier, so it returns long before a full baseline/enrichment
        poll would. Used right after the employee closes the manual login
        window, so the dashboard can show READY without waiting for
        :meth:`execute` to run the normal synchronization afterward.

        Reuses the same reader (and therefore the same
        ``_assert_authenticated``/``detect_auth_required`` logic) that
        :meth:`execute` uses -- there is exactly one implementation of
        "is OmegaFlow authenticated". Never raises: the boolean return is
        the only signal callers need, and the account's persisted
        ``session_status`` is always updated to match (READY,
        AUTH_REQUIRED, or ERROR on any other failure/timeout) so the
        dashboard immediately reflects an actionable state either way.
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

        async def _check() -> None:
            reader = await reader_cm.__aenter__()
            try:
                await reader.verify_authenticated()
            finally:
                with contextlib.suppress(Exception):
                    await reader_cm.__aexit__(None, None, None)

        try:
            await asyncio.wait_for(_check(), timeout=timeout_seconds)
        except BrowserProfileLockedError as exc:
            status, error = SessionStatus.ERROR, str(exc)
        except PortalAuthRequiredError as exc:
            status, error = SessionStatus.AUTH_REQUIRED, str(exc)
        except TimeoutError:
            status, error = (
                SessionStatus.ERROR,
                f"Vérification de session interrompue après {timeout_seconds:.0f}s.",
            )
        except Exception as exc:  # noqa: BLE001 - any other launch/read failure
            status, error = SessionStatus.ERROR, _short_error(exc)
        else:
            status, error = SessionStatus.READY, None

        self._mark_session_checked(account_id, status, now, error)
        return status is SessionStatus.READY

    def _mark_session_checked(
        self, account_id: int, status: SessionStatus, checked_at: datetime, error: str | None
    ) -> None:
        with self._uow_factory() as uow:
            uow.portal_accounts.mark_session_checked(
                account_id, status=status, checked_at=checked_at, error=error
            )
            uow.commit()

    def _record_failed_poll(self, account_id: int, started_at: datetime, error: str) -> SyncResult:
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
        return SyncResult(status=PollStatus.FAILED, error=error)

    async def _reconcile_and_enrich(
        self,
        reader: PortalReader,
        account_id: int,
        status: PollStatus,
        baseline_already_completed: bool,
        snapshot: QueueSnapshot,
        now: datetime,
    ) -> tuple[int, int, int, int, int]:
        rows_by_id = {row.record_id: row for row in snapshot.rows}

        with self._uow_factory() as uow:
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
        for dossier in dossiers_needing_detail.values():
            ref = _as_portal_ref(dossier)
            try:
                details = await reader.read_dossier_details(ref)
            except DetailReadError as exc:
                details_failed += 1
                with self._uow_factory() as uow:
                    uow.dossiers.save_details(dossier.id, _failed_details(str(exc)))
                    uow.commit()
                continue
            with self._uow_factory() as uow:
                uow.dossiers.save_details(dossier.id, details)
                uow.commit()

        return (
            len(result.to_create),
            len(result.to_reactivate),
            len(result.to_deactivate),
            notified,
            details_failed,
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
