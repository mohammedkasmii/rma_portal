"""SyncWorkflows: the use case behind one complete synchronization cycle.

One authenticated browser session is opened per cycle and every enabled workflow
is read sequentially inside it. Each workflow is independent:

* it has its own baseline (``workflows.baseline_completed_at``), so enabling a
  new queue never raises alerts for the dossiers it already contained;
* it is reconciled and committed in its own transaction, so a failed queue can
  neither stop nor invalidate a successful one;
* only a COMPLETE read may count absences -- PARTIAL, FAILED and AUTH_REQUIRED
  reads never mark a record absent.

What each observation means (new, returned, changed, left, informational) is
decided by the pure policy in ``domain.workflow_reconciliation``; this module
only sequences the I/O and persists the outcome together with its events,
alerts and outbox messages in one transaction.

The class also keeps the bounded session check (``verify_session``) and the
coalescing/locking behaviour of the pre-V2 ``SyncWorkflows``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    DetailReadError,
    PortalAuthRequiredError,
    PortalDossierRef,
    SyncResult,
    WorkflowQueueRow,
    WorkflowReadOutcome,
    WorkflowSnapshot,
    WorkflowSyncResult,
)
from rma_portal.application.ports import (
    CycleLock,
    PortalReader,
    PortalReaderFactory,
    UnitOfWork,
    UnitOfWorkFactory,
    WorkflowCatalog,
)
from rma_portal.domain.enums import (
    NotificationClass,
    OccurrenceOrigin,
    OutboxTopic,
    PollStatus,
    SessionStatus,
    SyncTrigger,
    WorkflowEventKind,
    WorkflowRulesStatus,
)
from rma_portal.domain.models import (
    Dossier,
    DossierDates,
    Workflow,
    WorkflowEvent,
    WorkflowMembership,
)
from rma_portal.domain.portal_dates import parse_french_date
from rma_portal.domain.sync_rules import ExistingDossierState, ReconciliationInput, reconcile
from rma_portal.domain.workflow_definition import COMMON_FIELD_KEYS, WorkflowDefinition
from rma_portal.domain.workflow_reconciliation import (
    Decision,
    decide_change,
    decide_departure,
    decide_first_appearance,
    decide_return,
    detect_material_change,
    fingerprint_fields,
    needs_detail_read,
)
from rma_portal.observability import new_operation_id, operation_context

logger = logging.getLogger(__name__)

_CLEANUP_TIMEOUT_SECONDS = 30.0
_MAX_ERROR_LENGTH = 500
_MAX_AI_JOBS_PER_WORKFLOW = 25

_UNCONFIRMED_CLEANUP_MESSAGE = (
    "Le nettoyage du navigateur après synchronisation a échoué ou a expiré ; le profil "
    "reste indisponible jusqu'au redémarrage du Portail RMA."
)

_LEGACY_DATE_KEYS = (
    "date_creation",
    "date_premiere_fin_prevue",
    "date_fin_travaux_prevue",
    "date_envoi_devis_garage",
    "date_photos_avant",
)


@dataclass(slots=True)
class _Counts:
    created: int = 0
    returned: int = 0
    changed: int = 0
    left: int = 0
    notifications: int = 0
    details_failed: int = 0
    baseline: bool = False


@dataclass(frozen=True, slots=True)
class _DetailTarget:
    dossier_id: int
    ref: PortalDossierRef
    forced: bool
    last_attempt: datetime | None


@dataclass(slots=True)
class _DetailBudget:
    """Shared by every workflow of one cycle: dedupes reads and bounds their number."""

    remaining: int
    done: set[str] = field(default_factory=set)


class SyncWorkflows:
    """Runs one complete multi-workflow synchronization cycle.

    A single instance is shared between the scheduler and any manual trigger,
    guaranteeing that only one cycle runs at a time in this process; an
    optional :class:`~rma_portal.application.ports.CycleLock` extends that
    guarantee across processes (PostgreSQL advisory lock).
    """

    def __init__(
        self,
        reader_factory: PortalReaderFactory,
        uow_factory: UnitOfWorkFactory,
        catalog: WorkflowCatalog,
        *,
        timezone_id: str = "Africa/Casablanca",
        max_detail_reads_per_cycle: int = 60,
        ai_jobs_enabled: bool = False,
        cycle_lock: CycleLock | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reader_factory = reader_factory
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._timezone_id = timezone_id
        self._max_details = max_detail_reads_per_cycle
        self._ai_jobs_enabled = ai_jobs_enabled
        self._cycle_lock = cycle_lock
        self._clock = clock
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Future[SyncResult] | None = None

    @property
    def is_running(self) -> bool:
        """True while a cycle (scheduled, manual, or connect-triggered) is in flight."""
        return self._lock.locked()

    async def execute(
        self,
        trigger: SyncTrigger = SyncTrigger.SCHEDULED,
        *,
        only_keys: frozenset[str] | None = None,
    ) -> SyncResult:
        if self._lock.locked() and self._inflight is not None:
            return await asyncio.shield(self._inflight)

        loop = asyncio.get_running_loop()
        inflight: asyncio.Future[SyncResult] = loop.create_future()
        self._inflight = inflight
        async with self._lock:
            # Every real execution (never the shield-only coalesce path above) gets its own
            # correlation ID, even when triggered right after a login (already "conn-*").
            with operation_context(new_operation_id("sync")):
                try:
                    result = await self._run_guarded(trigger, only_keys)
                except BaseException as exc:  # noqa: BLE001 - propagate after publishing
                    if not inflight.done():
                        inflight.set_exception(exc)
                    # Read the exception so asyncio never logs "Future exception was never
                    # retrieved" when no concurrent caller ever awaited the shielded future.
                    inflight.exception()
                    self._inflight = None
                    raise
                if not inflight.done():
                    inflight.set_result(result)
                self._inflight = None
                return result

    async def _run_guarded(
        self, trigger: SyncTrigger, only_keys: frozenset[str] | None
    ) -> SyncResult:
        if self._cycle_lock is None:
            return await self._execute_once(trigger, only_keys)
        with self._cycle_lock.try_acquire() as acquired:
            if not acquired:
                logger.info("stage=synchronization outcome=SKIPPED reason=cycle_running_elsewhere")
                return SyncResult(skipped=True, skip_reason="another synchronization cycle is running")
            return await self._execute_once(trigger, only_keys)

    # --- one cycle -----------------------------------------------------------------------------

    async def _execute_once(
        self, trigger: SyncTrigger, only_keys: frozenset[str] | None
    ) -> SyncResult:
        now = self._clock()
        sync_started = time.perf_counter()
        logger.info("stage=synchronization outcome=START trigger=%s", trigger)

        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                raise RuntimeError("no portal account is configured")
            if not account.enabled:
                return self._skipped(sync_started, "portal account disabled", "account_disabled")
            account_id = account.id
            previous_session_status = account.session_status
            plan = self._plan(uow.workflows.list_for_account(account_id, enabled_only=True), only_keys)

        if not plan:
            return self._skipped(sync_started, "no enabled workflow", "no_enabled_workflow")

        # A fresh installation (or one whose profile was wiped) has no captured OmegaFlow
        # session yet: skip without touching the browser/profile so "Se connecter" can take
        # the profile lock immediately instead of racing an automatic poll that would fail.
        if not self._reader_factory.has_saved_session():
            return self._skipped(sync_started, "no saved OmegaFlow session", "no_saved_session")

        # The browser is opened *before* any sync_runs row exists: a BrowserProfileLockedError
        # is a safe skip that must leave no record, and any other launch failure gets exactly
        # one FAILED row, created and finished together.
        try:
            reader_cm = self._reader_factory.open()
            reader = await reader_cm.__aenter__()
        except BrowserProfileLockedError as exc:
            return self._skipped(sync_started, str(exc), "profile_locked")
        except Exception as exc:  # noqa: BLE001 - any launch/context failure
            return self._record_failed_cycle(
                account_id, trigger, now, sync_started, _short_error(exc), len(plan)
            )

        try:
            with self._uow_factory() as uow:
                uow.portal_accounts.mark_poll_started(account_id, now)
                sync_run_id = uow.sync_runs.start(account_id, trigger, now).id
                assert sync_run_id is not None
                uow.commit()
        except Exception as exc:  # noqa: BLE001 - cannot even record the attempt
            if not await self._close_reader(reader_cm):
                return self._record_failed_cycle(
                    account_id, trigger, now, sync_started, _UNCONFIRMED_CLEANUP_MESSAGE, len(plan)
                )
            return self._record_failed_cycle(
                account_id, trigger, now, sync_started, _short_error(exc), len(plan)
            )

        results: list[WorkflowSyncResult] = []
        budget = _DetailBudget(remaining=self._max_details)
        try:
            for workflow, definition in plan:
                results.append(
                    await self._sync_workflow(reader, sync_run_id, workflow, definition, now, budget)
                )
        finally:
            cleanup_ok = await self._close_reader(reader_cm)

        return self._finish_cycle(
            account_id,
            sync_run_id,
            results,
            cleanup_ok=cleanup_ok,
            previous_session_status=previous_session_status,
            sync_started=sync_started,
        )

    def _plan(
        self, workflows: list[Workflow], only_keys: frozenset[str] | None
    ) -> list[tuple[Workflow, WorkflowDefinition]]:
        plan: list[tuple[Workflow, WorkflowDefinition]] = []
        for workflow in workflows:
            if workflow.rules_status is WorkflowRulesStatus.UNCONFIRMED:
                continue  # catalogued but inactive
            if only_keys is not None and workflow.key not in only_keys:
                continue
            definition = self._catalog.get(workflow.key)
            if definition is None:
                logger.warning("workflow %s has no catalog definition; skipped", workflow.key)
                continue
            plan.append((workflow, definition))
        return plan

    @staticmethod
    def _skipped(started: float, reason: str, code: str) -> SyncResult:
        logger.info(
            "stage=synchronization outcome=SKIPPED elapsed_ms=%.1f reason=%s",
            (time.perf_counter() - started) * 1000,
            code,
        )
        return SyncResult(status=None, skipped=True, skip_reason=reason)

    # --- one workflow -------------------------------------------------------------------------

    async def _sync_workflow(
        self,
        reader: PortalReader,
        sync_run_id: int,
        workflow: Workflow,
        definition: WorkflowDefinition,
        now: datetime,
        budget: _DetailBudget,
    ) -> WorkflowSyncResult:
        assert workflow.id is not None
        started = self._clock()
        with self._uow_factory() as uow:
            poll_run = uow.workflow_poll_runs.start(
                sync_run_id, workflow.id, started, baseline=workflow.baseline_completed_at is None
            )
            assert poll_run.id is not None
            poll_run_id = poll_run.id
            uow.commit()

        try:
            outcome = await reader.read_workflow(definition)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a reader must not raise, but never trust it
            logger.error("workflow %s reader raised unexpectedly", workflow.key, exc_info=True)
            outcome = WorkflowReadOutcome(
                workflow.key, PollStatus.FAILED, WorkflowSnapshot(workflow.key), _short_error(exc)
            )

        counts = _Counts()
        status, error = outcome.status, outcome.error
        targets: list[_DetailTarget] = []
        if status in (PollStatus.COMPLETE, PollStatus.PARTIAL):
            try:
                targets = self._reconcile(workflow, definition, outcome, poll_run_id, now, counts)
            except Exception as exc:  # noqa: BLE001 - the transaction rolled back; report FAILED
                logger.error("workflow %s reconciliation failed", workflow.key, exc_info=True)
                status, error = PollStatus.FAILED, _short_error(exc)
                counts = _Counts()
                targets = []

        if targets:
            await self._read_details(reader, definition, targets, budget, counts)

        completed_at = self._clock()
        self._finish_workflow(workflow, poll_run_id, status, error, outcome, counts, completed_at)
        logger.info(
            "stage=workflow_sync outcome=%s workflow=%s rows=%d created=%d returned=%d changed=%d "
            "left=%d notified=%d details_failed=%d baseline=%s",
            status,
            workflow.key,
            outcome.snapshot.rows_seen,
            counts.created,
            counts.returned,
            counts.changed,
            counts.left,
            counts.notifications,
            counts.details_failed,
            counts.baseline,
        )
        return WorkflowSyncResult(
            workflow_key=workflow.key,
            status=status,
            rows_seen=outcome.snapshot.rows_seen,
            pages_seen=outcome.snapshot.pages_seen,
            baseline=counts.baseline,
            created=counts.created,
            returned=counts.returned,
            changed=counts.changed,
            left=counts.left,
            notifications_created=counts.notifications,
            details_failed=counts.details_failed,
            error=error,
        )

    def _finish_workflow(
        self,
        workflow: Workflow,
        poll_run_id: int,
        status: PollStatus,
        error: str | None,
        outcome: WorkflowReadOutcome,
        counts: _Counts,
        completed_at: datetime,
    ) -> None:
        assert workflow.id is not None
        with self._uow_factory() as uow:
            uow.workflow_poll_runs.finish(
                poll_run_id,
                status=status,
                completed_at=completed_at,
                rows_seen=outcome.snapshot.rows_seen,
                pages_seen=outcome.snapshot.pages_seen,
                details_failed=counts.details_failed,
                created_count=counts.created,
                returned_count=counts.returned,
                changed_count=counts.changed,
                left_count=counts.left,
                notifications_created=counts.notifications,
                error=error,
                baseline=counts.baseline,
            )
            uow.workflows.mark_poll_finished(
                workflow.id, status=status, polled_at=completed_at, error=error
            )
            # Administrator alerts are edge-triggered: one event when a workflow starts
            # failing, not one per five-minute poll. They never touch memberships.
            if status is not workflow.last_poll_status:
                kind = {
                    PollStatus.PARTIAL: WorkflowEventKind.WORKFLOW_POLL_PARTIAL,
                    PollStatus.FAILED: WorkflowEventKind.WORKFLOW_POLL_FAILED,
                }.get(status)
                if kind is not None:
                    uow.workflow_events.add(
                        WorkflowEvent(
                            id=None,
                            kind=kind,
                            detected_at=completed_at,
                            notification_class=NotificationClass.ACTION,
                            workflow_id=workflow.id,
                            poll_run_id=poll_run_id,
                            message=(error or "")[:500] or None,
                        )
                    )
            uow.commit()

    # --- reconciliation -----------------------------------------------------------------------

    def _reconcile(
        self,
        workflow: Workflow,
        definition: WorkflowDefinition,
        outcome: WorkflowReadOutcome,
        poll_run_id: int,
        now: datetime,
        counts: _Counts,
    ) -> list[_DetailTarget]:
        """Apply one workflow's read to the database in a single transaction.

        Returns the dossiers whose shared detail page should now be read.
        """
        assert workflow.id is not None
        rows_by_id: Mapping[str, WorkflowQueueRow] = {r.record_id: r for r in outcome.snapshot.rows}
        alerts_enabled = workflow.baseline_completed_at is not None
        notification_class = workflow.notification_class
        targets: list[_DetailTarget | None] = []
        ai_jobs = 0

        with self._uow_factory() as uow:
            account_id = self._account_id(uow)
            memberships = uow.workflow_memberships.by_record_id(workflow.id)
            plan = reconcile(
                ReconciliationInput(
                    poll_status=outcome.status,
                    baseline_already_completed=alerts_enabled,
                    seen_record_ids=frozenset(rows_by_id),
                    existing={
                        record_id: ExistingDossierState(
                            record_id, m.active, m.missing_complete_polls
                        )
                        for record_id, m in memberships.items()
                    },
                )
            )

            def record(
                decision: Decision,
                *,
                membership: WorkflowMembership,
                occurrence_id: int,
                changed: tuple[str, ...] = (),
                before: Mapping[str, str] | None = None,
                after: Mapping[str, str] | None = None,
            ) -> None:
                nonlocal ai_jobs
                if decision.event is None:
                    return
                event = uow.workflow_events.add(
                    WorkflowEvent(
                        id=None,
                        kind=decision.event,
                        detected_at=now,
                        notification_class=notification_class,
                        workflow_id=workflow.id,
                        membership_id=membership.id,
                        occurrence_id=occurrence_id,
                        dossier_id=membership.dossier_id,
                        poll_run_id=poll_run_id,
                        changed_fields=changed,
                        before_fingerprints=before or {},
                        after_fingerprints=after or {},
                    )
                )
                if decision.notification is None:
                    return
                notification = uow.notifications.create_for_occurrence(
                    dossier_id=membership.dossier_id,
                    workflow_id=workflow.id,
                    occurrence_id=occurrence_id,
                    kind=decision.notification,
                    detected_at=now,
                    event_id=event.id,
                )
                counts.notifications += 1
                uow.outbox.add(
                    OutboxTopic.NOTIFICATION_CREATED,
                    {
                        "notification_id": notification.id,
                        "workflow_id": workflow.id,
                        "occurrence_id": occurrence_id,
                        "kind": decision.notification.value,
                    },
                    now,
                )
                if self._ai_jobs_enabled and ai_jobs < _MAX_AI_JOBS_PER_WORKFLOW:
                    ai_jobs += 1
                    uow.outbox.add(
                        OutboxTopic.AI_JOB_REQUESTED,
                        {
                            "feature": "HIGHLIGHT_EXPLANATION",
                            "occurrence_id": occurrence_id,
                            "event_id": event.id,
                        },
                        now,
                    )

            new_arrivals: list[WorkflowMembership] = []

            for record_id in sorted(plan.to_create):
                row = rows_by_id[record_id]
                dossier, _, _ = self._upsert_dossier(uow, account_id, definition, row, now)
                membership = uow.workflow_memberships.create(
                    workflow_id=workflow.id,
                    dossier_id=dossier.id,
                    seen_at=now,
                    captured_fields=row.fields,
                    fingerprint=fingerprint_fields(row.fields),
                )
                uow.workflow_work.create_initial(membership.id, now)
                origin = OccurrenceOrigin.NEW if alerts_enabled else OccurrenceOrigin.BASELINE
                occurrence = uow.workflow_occurrences.create(
                    membership_id=membership.id,
                    workflow_id=workflow.id,
                    dossier_id=dossier.id,
                    occurrence_number=1,
                    origin=origin,
                    detected_at=now,
                )
                decision = decide_first_appearance(notification_class, alerts_enabled=alerts_enabled)
                record(decision, membership=membership, occurrence_id=occurrence.id)
                counts.created += 1
                if decision.event is not None:
                    new_arrivals.append(membership)
                targets.append(self._target(definition, dossier, forced=True, now=now, row=row))

            for record_id in sorted(plan.to_reactivate):
                row = rows_by_id[record_id]
                existing = memberships[record_id]
                dossier, _, _ = self._upsert_dossier(uow, account_id, definition, row, now)
                number = uow.workflow_memberships.reactivate(
                    existing.id,
                    seen_at=now,
                    captured_fields=row.fields,
                    fingerprint=fingerprint_fields(row.fields),
                )
                occurrence = uow.workflow_occurrences.create(
                    membership_id=existing.id,
                    workflow_id=workflow.id,
                    dossier_id=dossier.id,
                    occurrence_number=number,
                    origin=OccurrenceOrigin.RETURNED,
                    detected_at=now,
                )
                record(
                    decide_return(notification_class, alerts_enabled=alerts_enabled),
                    membership=existing,
                    occurrence_id=occurrence.id,
                )
                counts.returned += 1
                targets.append(self._target(definition, dossier, forced=True, now=now, row=row))

            for record_id in sorted(plan.to_touch):
                row = rows_by_id[record_id]
                existing = memberships[record_id]
                dossier, _, status_changed = self._upsert_dossier(
                    uow, account_id, definition, row, now
                )
                change = detect_material_change(
                    existing.captured_fields,
                    row.fields,
                    material_fields=definition.material_fields,
                    alert_fields=definition.alert_fields,
                )
                uow.workflow_memberships.refresh(
                    existing.id,
                    seen_at=now,
                    captured_fields=row.fields,
                    fingerprint=fingerprint_fields(row.fields),
                    changed=change is not None,
                )
                decision = decide_change(notification_class, change, alerts_enabled=alerts_enabled)
                if decision.event is not None and change is not None:
                    occurrence = self._latest_occurrence(uow, existing, workflow, now)
                    record(
                        decision,
                        membership=existing,
                        occurrence_id=occurrence,
                        changed=change.changed_fields,
                        before=change.before,
                        after=change.after,
                    )
                    counts.changed += 1
                targets.append(
                    self._target(definition, dossier, forced=status_changed, now=now, row=row)
                )

            for record_id, count in plan.absence_increments.items():
                uow.workflow_memberships.apply_absence_increment(memberships[record_id].id, count)

            for record_id in sorted(plan.to_deactivate):
                departing = memberships[record_id]
                uow.workflow_memberships.deactivate(departing.id)
                occurrence = self._latest_occurrence(uow, departing, workflow, now)
                record(
                    decide_departure(notification_class, alerts_enabled=alerts_enabled),
                    membership=departing,
                    occurrence_id=occurrence,
                )
                counts.left += 1
                others = [
                    m
                    for m in uow.workflow_memberships.list_for_dossier(departing.dossier_id)
                    if m.active and m.id != departing.id
                ]
                if not others:
                    uow.dossiers.set_active(departing.dossier_id, False)

            if alerts_enabled:
                self._record_transitions(uow, account_id, definition, workflow, new_arrivals, now, poll_run_id)

            if plan.is_baseline_poll:
                uow.workflows.mark_baseline_completed(workflow.id, now)
                counts.baseline = True
            uow.commit()
        return [target for target in targets if target is not None]

    @staticmethod
    def _account_id(uow: UnitOfWork) -> int:
        account = uow.portal_accounts.get_default()
        if account is None or account.id is None:
            raise RuntimeError("no portal account is configured")
        return account.id

    @staticmethod
    def _upsert_dossier(
        uow: UnitOfWork,
        account_id: int,
        definition: WorkflowDefinition,
        row: WorkflowQueueRow,
        now: datetime,
    ) -> tuple[Dossier, bool, bool]:
        common = {key: value for key, value in row.fields.items() if key in COMMON_FIELD_KEYS}
        return uow.dossiers.upsert_from_workflow_row(
            account_id, row.record_id, row.details_href, common, now
        )

    @staticmethod
    def _latest_occurrence(
        uow: UnitOfWork, membership: WorkflowMembership, workflow: Workflow, now: datetime
    ) -> int:
        assert workflow.id is not None
        latest = uow.workflow_occurrences.latest_for_membership(membership.id)
        if latest is not None and latest.id is not None:
            return latest.id
        # A membership that predates occurrences: give it the immutable identity it lacks.
        created = uow.workflow_occurrences.create(
            membership_id=membership.id,
            workflow_id=workflow.id,
            dossier_id=membership.dossier_id,
            occurrence_number=max(membership.occurrence_number, 1),
            origin=OccurrenceOrigin.BASELINE,
            detected_at=membership.first_seen_at,
        )
        assert created.id is not None
        return created.id

    def _record_transitions(
        self,
        uow: UnitOfWork,
        account_id: int,
        definition: WorkflowDefinition,
        workflow: Workflow,
        arrivals: list[WorkflowMembership],
        now: datetime,
        poll_run_id: int,
    ) -> None:
        """A dossier newly appearing in a documented downstream stage adds an activity event to
        its upstream membership. Upstream work status is never changed."""
        if not definition.follows or not arrivals:
            return
        for upstream_key in definition.follows:
            upstream = uow.workflows.get_by_key(account_id, upstream_key)
            if upstream is None or upstream.id is None:
                continue
            for arrival in arrivals:
                source = uow.workflow_memberships.get(upstream.id, arrival.dossier_id)
                if source is None or not source.active:
                    continue
                occurrence = self._latest_occurrence(uow, source, upstream, now)
                uow.workflow_events.add(
                    WorkflowEvent(
                        id=None,
                        kind=WorkflowEventKind.WORKFLOW_ITEM_TRANSITION,
                        detected_at=now,
                        notification_class=NotificationClass.INFORMATIONAL,
                        workflow_id=upstream.id,
                        membership_id=source.id,
                        occurrence_id=occurrence,
                        dossier_id=arrival.dossier_id,
                        poll_run_id=poll_run_id,
                        message=f"Apparu dans « {workflow.name} »",
                    )
                )

    # --- details --------------------------------------------------------------------------------

    @staticmethod
    def _target(
        definition: WorkflowDefinition,
        dossier: Dossier,
        *,
        forced: bool,
        now: datetime,
        row: WorkflowQueueRow,
    ) -> _DetailTarget | None:
        """A detail-read target, or ``None`` when this workflow does not need the detail page."""
        if not row.details_href and not dossier.details_href:
            return None
        if not needs_detail_read(
            definition,
            dossier.detail_fields,
            dossier.detail_fetched_at,
            failed=dossier.detail_error is not None,
            forced=forced,
            now=now,
        ):
            return None
        assert dossier.id is not None
        return _DetailTarget(
            dossier_id=dossier.id,
            ref=PortalDossierRef(dossier.record_id, row.details_href or dossier.details_href),
            forced=forced,
            last_attempt=dossier.detail_fetched_at,
        )

    async def _read_details(
        self,
        reader: PortalReader,
        definition: WorkflowDefinition,
        targets: list[_DetailTarget],
        budget: _DetailBudget,
        counts: _Counts,
    ) -> None:
        """Read shared detail pages, once per record per cycle across every workflow.

        New/changed dossiers first, then the ones read longest ago; whatever exceeds the
        per-cycle budget is simply picked up by the next cycle.
        """
        ordered = sorted(
            targets,
            key=lambda t: (0 if t.forced else 1, t.last_attempt or datetime.min.replace(tzinfo=UTC)),
        )
        fields = self._catalog.shared_detail_fields()
        for target in ordered:
            if target.ref.record_id in budget.done:
                continue
            if budget.remaining <= 0:
                logger.info("detail budget exhausted for this cycle; deferring the remainder")
                return
            budget.remaining -= 1
            budget.done.add(target.ref.record_id)
            attempted_at = self._clock()
            try:
                detail = await reader.read_dossier_detail_fields(target.ref, fields)
            except DetailReadError as exc:
                counts.details_failed += 1
                with self._uow_factory() as uow:
                    uow.dossiers.mark_detail_failed(target.dossier_id, str(exc), attempted_at)
                    uow.commit()
                continue
            except PortalAuthRequiredError:
                # The session ended mid-cycle: the list data already reconciled is valid, the
                # next workflow's own read will report AUTH_REQUIRED. Stop reading details.
                counts.details_failed += 1
                return
            with self._uow_factory() as uow:
                uow.dossiers.save_detail_values(
                    target.dossier_id,
                    detail.values,
                    self._dates_from(detail.values),
                    attempted_at,
                )
                uow.commit()

    def _dates_from(self, values: Mapping[str, str]) -> DossierDates:
        parsed: dict[str, object] = {}
        for key in _LEGACY_DATE_KEYS:
            date, raw = parse_french_date(values.get(key, ""), self._timezone_id)
            parsed[key] = date
            parsed[f"{key}_raw"] = raw
        return DossierDates(**parsed)  # type: ignore[arg-type]

    # --- cycle bookkeeping ----------------------------------------------------------------------

    def _finish_cycle(
        self,
        account_id: int,
        sync_run_id: int,
        results: list[WorkflowSyncResult],
        *,
        cleanup_ok: bool,
        previous_session_status: SessionStatus,
        sync_started: float,
    ) -> SyncResult:
        status = _aggregate_status(results)
        error = _aggregate_error(results, status)
        if not cleanup_ok:
            # An unconfirmed browser teardown overrides whatever the reads found: this session can
            # no longer be accounted for. Already reconciled data is deliberately left untouched.
            status, error = PollStatus.FAILED, _UNCONFIRMED_CLEANUP_MESSAGE
        completed_at = self._clock()
        by_status = {s: sum(1 for r in results if r.status is s) for s in PollStatus}
        with self._uow_factory() as uow:
            uow.sync_runs.finish(
                sync_run_id,
                status=status,
                completed_at=completed_at,
                workflows_total=len(results),
                workflows_complete=by_status[PollStatus.COMPLETE],
                workflows_partial=by_status[PollStatus.PARTIAL],
                workflows_auth_required=by_status[PollStatus.AUTH_REQUIRED],
                workflows_failed=by_status[PollStatus.FAILED],
                error=error,
            )
            uow.portal_accounts.mark_poll_finished(
                account_id, status=status, polled_at=completed_at, error=error
            )
            if (
                by_status[PollStatus.AUTH_REQUIRED]
                and previous_session_status is not SessionStatus.AUTH_REQUIRED
            ):
                uow.workflow_events.add(
                    WorkflowEvent(
                        id=None,
                        kind=WorkflowEventKind.SESSION_AUTH_REQUIRED,
                        detected_at=completed_at,
                        notification_class=NotificationClass.ACTION,
                        message="La session OmegaFlow doit être reconnectée.",
                    )
                )
            uow.commit()

        summary = logger.info if status is PollStatus.COMPLETE else logger.warning
        summary(
            "stage=synchronization outcome=%s elapsed_ms=%.1f workflows=%d complete=%d partial=%d "
            "auth_required=%d failed=%d",
            status,
            (time.perf_counter() - sync_started) * 1000,
            len(results),
            by_status[PollStatus.COMPLETE],
            by_status[PollStatus.PARTIAL],
            by_status[PollStatus.AUTH_REQUIRED],
            by_status[PollStatus.FAILED],
        )
        return SyncResult(
            status=status,
            rows_seen=sum(r.rows_seen for r in results),
            pages_seen=sum(r.pages_seen for r in results),
            details_failed=sum(r.details_failed for r in results),
            error=error,
            created=sum(r.created for r in results),
            reactivated=sum(r.returned for r in results),
            deactivated=sum(r.left for r in results),
            changed=sum(r.changed for r in results),
            notifications_created=sum(r.notifications_created for r in results),
            sync_run_id=sync_run_id,
            workflows=tuple(results),
        )

    def _record_failed_cycle(
        self,
        account_id: int,
        trigger: SyncTrigger,
        started_at: datetime,
        perf_started: float,
        error: str,
        workflows_total: int,
    ) -> SyncResult:
        """A cycle that never got a browser session: one FAILED row, created and finished in
        the same transaction so nothing is ever left unfinished. Existing data is untouched."""
        completed_at = self._clock()
        with self._uow_factory() as uow:
            uow.portal_accounts.mark_poll_started(account_id, started_at)
            run = uow.sync_runs.start(account_id, trigger, started_at)
            uow.sync_runs.finish(
                run.id,
                status=PollStatus.FAILED,
                completed_at=completed_at,
                workflows_total=workflows_total,
                workflows_complete=0,
                workflows_partial=0,
                workflows_auth_required=0,
                workflows_failed=0,
                error=error,
            )
            uow.portal_accounts.mark_poll_finished(
                account_id, status=PollStatus.FAILED, polled_at=completed_at, error=error
            )
            uow.workflow_events.add(
                WorkflowEvent(
                    id=None,
                    kind=WorkflowEventKind.WORKFLOW_POLL_FAILED,
                    detected_at=completed_at,
                    notification_class=NotificationClass.ACTION,
                    message=error[:500],
                )
            )
            uow.commit()
            sync_run_id = run.id
        logger.warning(
            "stage=synchronization outcome=FAILED elapsed_ms=%.1f workflows=0",
            (time.perf_counter() - perf_started) * 1000,
        )
        return SyncResult(status=PollStatus.FAILED, error=error, sync_run_id=sync_run_id)

    async def _close_reader(self, reader_cm) -> bool:
        """Bounded, best-effort close -- returns whether it was confirmed to finish within
        :data:`_CLEANUP_TIMEOUT_SECONDS`.

        A plain, unbounded ``await reader_cm.__aexit__(...)`` would let a stalled
        browser-manager teardown keep this cycle (and therefore ``is_running``) pending
        forever. The reader itself keeps the profile lock held whenever its own teardown
        cannot be confirmed; this only reports the outcome so the cycle finishes as
        FAILED/ERROR, never as a false success.
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

    # --- session verification (unchanged behaviour) -----------------------------------------------

    async def verify_session(self, timeout_seconds: float) -> bool:
        """Short, bounded, read-only check that the saved profile is still authenticated.

        Deliberately does *not* read any queue or dossier, so it returns long before a full
        cycle would. Used right after the employee closes the manual login window, so the
        dashboard can show READY without waiting for :meth:`execute`.

        Reuses the same reader (and therefore the same authentication detection and positive
        evidence check) the synchronization uses -- there is exactly one implementation of "is
        OmegaFlow authenticated". Never raises for an ordinary failure: the boolean is the only
        signal callers need and the account's persisted ``session_status`` always matches
        (READY, AUTH_REQUIRED, or ERROR on any other failure/timeout).

        Browser startup, the auth check and cleanup are each bounded by their own independent
        ``timeout_seconds`` wait -- three separate ``asyncio.wait_for`` calls, not one wrapping
        all three, since a stall inside cleanup would otherwise run unbounded after the wrapped
        coroutine was already cancelled once. A cleanup failure always wins over what the check
        found: this method never reports READY for a browser session it can no longer account for.
        """
        now = self._clock()
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
            # Its own bound, never skipped just because startup/the check failed, timed out, or
            # the whole method is being cancelled (app shutdown).
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
            status, error = (
                SessionStatus.ERROR,
                "Le nettoyage du navigateur après vérification de session a échoué ou "
                "a expiré ; le profil reste indisponible jusqu'au redémarrage du "
                "Portail RMA.",
            )

        self._mark_session_checked(account_id, status, now, error)
        return status is SessionStatus.READY

    async def mark_login_teardown_failed(self, message: str) -> None:
        """Persist ERROR for a visible login browser whose teardown could not be confirmed.

        Reuses the exact account-status persistence :meth:`verify_session` uses, so
        ``build_session_view`` reflects the failure immediately.
        """
        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                return
            account_id = account.id
        self._mark_session_checked(account_id, SessionStatus.ERROR, self._clock(), message)

    def _mark_session_checked(
        self, account_id: int, status: SessionStatus, checked_at: datetime, error: str | None
    ) -> None:
        with self._uow_factory() as uow:
            uow.portal_accounts.mark_session_checked(
                account_id, status=status, checked_at=checked_at, error=error
            )
            uow.commit()


def _aggregate_status(results: list[WorkflowSyncResult]) -> PollStatus:
    """COMPLETE only when every workflow was; AUTH_REQUIRED/FAILED only when none produced data."""
    statuses = {r.status for r in results}
    if not statuses or statuses == {PollStatus.COMPLETE}:
        return PollStatus.COMPLETE
    if statuses == {PollStatus.AUTH_REQUIRED}:
        return PollStatus.AUTH_REQUIRED
    if statuses == {PollStatus.FAILED}:
        return PollStatus.FAILED
    if statuses <= {PollStatus.AUTH_REQUIRED, PollStatus.FAILED}:
        return PollStatus.FAILED
    return PollStatus.PARTIAL


def _aggregate_error(results: list[WorkflowSyncResult], status: PollStatus) -> str | None:
    if status is PollStatus.COMPLETE:
        return None
    problems = [r for r in results if r.status is not PollStatus.COMPLETE and r.error]
    if not problems:
        return None
    first = problems[0]
    if len(problems) == 1:
        return f"{first.workflow_key}: {first.error}"[:_MAX_ERROR_LENGTH]
    return f"{len(problems)} workflows en anomalie ; premier : {first.workflow_key}: {first.error}"[
        :_MAX_ERROR_LENGTH
    ]


def _short_error(exc: BaseException) -> str:
    """A short technical message safe to store in sync_runs/last_error."""
    message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return message[:_MAX_ERROR_LENGTH]
