"""SyncAgreementQueue: the orchestration use case for one OmegaFlow poll.

This module wires I/O (the portal reader, the database) to the pure policy
in ``domain.sync_rules``. It never talks to SQLAlchemy or Camoufox directly
-- only through the ``application.ports`` protocols -- so it can be tested
with in-memory fakes.
"""

from __future__ import annotations

import asyncio
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
from rma_portal.domain.enums import NotificationKind, PollStatus
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
            uow.portal_accounts.mark_poll_started(account_id, now)
            poll_run = uow.poll_runs.start(account_id, now)
            poll_run_id = poll_run.id
            uow.commit()

        try:
            reader_cm = self._reader_factory.open()
        except BrowserProfileLockedError as exc:
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))

        snapshot = QueueSnapshot()
        status: PollStatus
        error: str | None = None

        try:
            async with reader_cm as reader:
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
        except BrowserProfileLockedError as exc:
            return SyncResult(status=None, skipped=True, skip_reason=str(exc))

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
            # be fetched twice and double-count `details_failed`.
            dossiers_needing_detail: list = list(uow.dossiers.dossiers_needing_detail_retry(account_id))
            notified = 0

            for record_id in result.to_create:
                dossier = uow.dossiers.create_from_row(account_id, rows_by_id[record_id], now)
                dossiers_needing_detail.append(dossier)
                if result.create_notifications:
                    uow.notifications.create(dossier.id, NotificationKind.NEW_AGREEMENT_DOSSIER, now)
                    notified += 1

            for record_id in result.to_reactivate:
                dossier = uow.dossiers.reactivate(account_id, rows_by_id[record_id], now)
                dossiers_needing_detail.append(dossier)
                if result.create_notifications:
                    uow.notifications.create(dossier.id, NotificationKind.NEW_AGREEMENT_DOSSIER, now)
                    notified += 1

            for record_id in result.to_touch:
                uow.dossiers.touch(account_id, rows_by_id[record_id], now)

            for record_id, count in result.absence_increments.items():
                uow.dossiers.apply_absence_increment(account_id, record_id, count)

            for record_id in result.to_deactivate:
                uow.dossiers.deactivate(account_id, record_id)

            if result.is_baseline_poll:
                uow.portal_accounts.mark_baseline_completed(account_id, now)

            uow.commit()

        details_failed = 0
        for dossier in dossiers_needing_detail:
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


def _failed_details(error: str) -> DossierDetails:
    return DossierDetails(dates=DossierDates(), detail_complete=False, detail_error=error)
