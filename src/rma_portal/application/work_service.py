"""Employee-facing use cases behind the V2 JSON API.

Everything here presents stored facts: memberships, occurrences, events, work
status and notes. Reading never acknowledges anything -- acknowledgement is an
explicit call made when an employee opens an occurrence, and it only ever
touches that employee's read state.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from rma_portal.application.dossier_service import DossierNotFoundError
from rma_portal.application.ports import UnitOfWork, UnitOfWorkFactory, WorkflowCatalog
from rma_portal.application.read_models import EventView, InboxRecord, WorkflowStat
from rma_portal.application.views import (
    ColumnView,
    CountersView,
    DashboardView,
    DossierCommonView,
    DossierHitView,
    DossierView,
    EventOut,
    FacetsView,
    FieldValue,
    ItemPage,
    ItemView,
    LastRunView,
    MembershipView,
    NoteView,
    OccurrenceView,
    PollRunView,
    SessionHealthView,
    SyncHealthView,
    SyncRunView,
    WorkflowDetailView,
    WorkflowView,
    WorkStateView,
)
from rma_portal.domain.enums import (
    MAX_NOTE_LENGTH,
    NotificationClass,
    NotificationKind,
    OutboxTopic,
    PollStatus,
    SessionStatus,
    WorkflowRulesStatus,
    WorkStatus,
)
from rma_portal.domain.models import (
    EmptyNoteError,
    NoteTooLongError,
    SyncRun,
    WorkflowPollRun,
)
from rma_portal.domain.portal_dates import parse_french_date
from rma_portal.domain.workflow_definition import COMMON_FIELD_KEYS, WorkflowDefinition


class WorkflowNotFoundError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"workflow {key!r} not found")
        self.key = key


class MembershipNotFoundError(Exception):
    def __init__(self, membership_id: int) -> None:
        super().__init__(f"membership {membership_id} not found")
        self.membership_id = membership_id


class OccurrenceNotFoundError(Exception):
    def __init__(self, occurrence_id: int) -> None:
        super().__init__(f"occurrence {occurrence_id} not found")
        self.occurrence_id = occurrence_id


_SESSION_LABELS = {
    "UNKNOWN": "Session non vérifiée",
    "READY": "Session OmegaFlow active",
    "AUTH_REQUIRED": "Reconnexion à OmegaFlow requise",
    "ERROR": "Erreur de session OmegaFlow",
    "CONNECTING": "Connexion en cours",
    "VERIFYING": "Vérification de la session",
}

_WORK_ORDER = {
    WorkStatus.TO_DO: 0,
    WorkStatus.IN_PROGRESS: 1,
    WorkStatus.WAITING: 2,
    WorkStatus.DONE: 3,
}

_MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class ItemQuery:
    workflow_keys: tuple[str, ...] = ()
    category: str | None = None
    search: str | None = None
    unread_only: bool = False
    unread_kind: str | None = None
    work_status: WorkStatus | None = None
    portal_status: str | None = None
    procedure: str | None = None
    sort: str = "default"
    descending: bool = True
    page: int = 1
    page_size: int = 25


class WorkService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: WorkflowCatalog,
        *,
        base_url: str,
        timezone_id: str = "Africa/Casablanca",
        connect_url: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._base_url = base_url
        self._tz = timezone_id
        self._connect_url = connect_url
        self._clock = clock

    # --- presentation helpers -------------------------------------------------------------------

    def _definition(self, key: str) -> WorkflowDefinition | None:
        return self._catalog.get(key)

    def _url(self, route: str, record_id: str) -> str:
        return f"{self._base_url.rstrip('/')}/{route.rstrip('/')}/view-dossier-details/{record_id}/"

    def _workflow_view(self, stat: WorkflowStat) -> WorkflowView:
        last_run = None
        if stat.last_run_rows is not None:
            last_run = LastRunView(
                status=stat.last_poll_status,
                baseline=bool(stat.last_run_baseline),
                rows_seen=stat.last_run_rows or 0,
                created=stat.last_run_created or 0,
                returned=stat.last_run_returned or 0,
                changed=stat.last_run_changed or 0,
                left=stat.last_run_left or 0,
                details_failed=stat.last_run_details_failed or 0,
            )
        return WorkflowView(
            key=stat.key,
            name=stat.name,
            category=stat.category,
            route=stat.route,
            view_id=stat.view_id,
            notification_class=stat.notification_class,
            rules_status=stat.rules_status,
            needs_site_validation=stat.rules_status is WorkflowRulesStatus.CAPTURE_DERIVED,
            enabled=stat.enabled,
            baseline_completed_at=stat.baseline_completed_at,
            last_poll_at=stat.last_poll_at,
            last_success_at=stat.last_success_at,
            last_poll_status=stat.last_poll_status,
            last_error=stat.last_error,
            active_count=stat.active_count,
            unread_new=stat.unread_new,
            unread_changed=stat.unread_changed,
            by_work_status={s.value: n for s, n in stat.by_work_status.items()},
            last_run=last_run,
        )

    def _event_out(self, event: EventView) -> EventOut:
        labels: list[str] = []
        definition = self._definition(event.workflow_key) if event.workflow_key else None
        for key in event.changed_fields:
            try:
                labels.append(definition.spec(key).label if definition else key)
            except KeyError:
                labels.append(key)
        return EventOut(
            id=event.id,
            kind=event.kind,
            notification_class=event.notification_class,
            detected_at=event.detected_at,
            workflow_key=event.workflow_key,
            workflow_name=event.workflow_name,
            dossier_id=event.dossier_id,
            dossier_number=event.dossier_number,
            insured_name=event.insured_name,
            occurrence_id=event.occurrence_id,
            membership_id=event.membership_id,
            changed_fields=list(event.changed_fields),
            changed_labels=labels,
            message=event.message,
        )

    def _primary_date(
        self, definition: WorkflowDefinition | None, record: InboxRecord
    ) -> tuple[datetime | None, str, str | None]:
        if definition is None:
            return None, "", None
        values = {**record.detail_fields, **record.captured_fields}
        for key in definition.primary_date:
            parsed, raw = parse_french_date(values.get(key, ""), self._tz)
            if parsed is not None:
                return parsed, raw, definition.spec(key).label
        return None, "", None

    def _item(self, record: InboxRecord, now: datetime) -> ItemView:
        definition = self._definition(record.workflow_key)
        primary, raw, label = self._primary_date(definition, record)
        fields: dict[str, str] = {}
        if definition is not None:
            for spec in definition.list_fields:
                if spec.key not in COMMON_FIELD_KEYS:
                    fields[spec.key] = record.captured_fields.get(spec.key, "")
        kind = record.unread_kind
        return ItemView(
            workflow_key=record.workflow_key,
            workflow_name=record.workflow_name,
            workflow_category=record.workflow_category,
            notification_class=record.notification_class,
            rules_status=record.rules_status,
            membership_id=record.membership_id,
            occurrence_id=record.occurrence_id,
            occurrence_number=record.occurrence_number,
            occurrence_origin=record.occurrence_origin,
            dossier_id=record.dossier_id,
            record_id=record.record_id,
            dossier_number=record.dossier_number,
            insured_name=record.insured_name,
            registration=record.registration,
            garage=record.garage,
            procedure=record.procedure,
            portal_status=record.portal_status,
            city=record.city,
            active=record.active,
            detected_at=record.occurrence_detected_at,
            primary_date=primary,
            primary_date_raw=raw,
            primary_date_label=label,
            queue_age_days=max(0, (now - record.occurrence_detected_at).days),
            work_status=record.work_status,
            work_version=record.work_version,
            unread=record.unread,
            unread_kind=kind.value if kind else None,
            fields=fields,
            omegaflow_url=self._url(record.workflow_route, record.record_id),
        )

    @staticmethod
    def _columns(definition: WorkflowDefinition) -> list[ColumnView]:
        return [
            ColumnView(spec.key, spec.label, spec.kind.value, spec.date)
            for spec in definition.list_fields
            if spec.key not in COMMON_FIELD_KEYS
        ]

    # --- dashboard and workflows ----------------------------------------------------------------

    def _account_id(self, uow: UnitOfWork) -> int:
        account = uow.portal_accounts.get_default()
        if account is None or account.id is None:
            raise RuntimeError("no portal account is configured")
        return account.id

    def session_health(self, live_state: str | None = None) -> SessionHealthView:
        with self._uow_factory() as uow:
            return self._session_health(uow, live_state)

    def _session_health(self, uow: UnitOfWork, live_state: str | None = None) -> SessionHealthView:
        account = uow.portal_accounts.get_default()
        state = live_state or (account.session_status.value if account else SessionStatus.UNKNOWN.value)
        latest = uow.sync_runs.latest(account.id) if account and account.id else None
        syncing = (latest is not None and latest.completed_at is None) or uow.outbox.has_pending(
            OutboxTopic.SYNC_REQUESTED
        )
        return SessionHealthView(
            state=state,
            label=_SESSION_LABELS.get(state, state),
            last_poll_at=account.last_poll_at if account else None,
            last_success_at=account.last_success_at if account else None,
            last_error=account.last_error if account else None,
            syncing=syncing,
            connect_url=self._connect_url,
        )

    @staticmethod
    def _sync_run_view(run: SyncRun | None) -> SyncRunView | None:
        if run is None or run.id is None:
            return None
        return SyncRunView(
            id=run.id,
            trigger=run.trigger.value,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            workflows_total=run.workflows_total,
            workflows_complete=run.workflows_complete,
            workflows_partial=run.workflows_partial,
            workflows_auth_required=run.workflows_auth_required,
            workflows_failed=run.workflows_failed,
            error=run.error,
        )

    def workflows(self, user_id: int) -> list[WorkflowView]:
        with self._uow_factory() as uow:
            stats = uow.queries.workflow_stats(user_id, self._account_id(uow))
        return [self._workflow_view(s) for s in stats]

    def dashboard(self, user_id: int, live_state: str | None = None) -> DashboardView:
        with self._uow_factory() as uow:
            account_id = self._account_id(uow)
            stats = uow.queries.workflow_stats(user_id, account_id)
            session = self._session_health(uow, live_state)
            last_run = self._sync_run_view(uow.sync_runs.latest(account_id))
            activity = uow.queries.events(limit=25)
            alerts = uow.queries.events(limit=5, operational=True)
        workflows = [self._workflow_view(s) for s in stats]
        active = [w for w in workflows if w.enabled]
        totals: Counter[str] = Counter()
        for workflow in active:
            totals.update(workflow.by_work_status)
        problem = sum(
            1
            for w in active
            if w.last_poll_status in (PollStatus.PARTIAL, PollStatus.FAILED, PollStatus.AUTH_REQUIRED)
        )
        return DashboardView(
            session=session,
            last_run=last_run,
            counters=CountersView(
                actionable_new=sum(w.unread_new for w in active),
                changed_unread=sum(w.unread_changed for w in active),
                active_total=sum(w.active_count for w in active),
                problem_workflows=problem,
                by_work_status=dict(totals),
            ),
            workflows=workflows,
            activity=[self._event_out(e) for e in activity],
            alerts=[self._event_out(e) for e in alerts],
        )

    def workflow_detail(self, user_id: int, key: str) -> WorkflowDetailView:
        with self._uow_factory() as uow:
            stats = uow.queries.workflow_stats(user_id, self._account_id(uow))
            stat = next((s for s in stats if s.key == key), None)
            if stat is None:
                raise WorkflowNotFoundError(key)
            runs = uow.workflow_poll_runs.recent_for_workflow(stat.workflow_id, limit=10)
            events = uow.queries.events(limit=25, workflow_ids=[stat.workflow_id])
        definition = self._definition(key)
        return WorkflowDetailView(
            workflow=self._workflow_view(stat),
            filter_label=definition.filter.label if definition and definition.filter else None,
            primary_date_labels=(
                [definition.spec(k).label for k in definition.primary_date] if definition else []
            ),
            columns=self._columns(definition) if definition else [],
            recent_runs=[self._poll_run_view(r) for r in runs],
            recent_events=[self._event_out(e) for e in events],
        )

    @staticmethod
    def _poll_run_view(run: WorkflowPollRun) -> PollRunView:
        assert run.id is not None
        return PollRunView(
            id=run.id,
            sync_run_id=run.sync_run_id,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            baseline=run.baseline,
            rows_seen=run.rows_seen,
            pages_seen=run.pages_seen,
            created=run.created_count,
            returned=run.returned_count,
            changed=run.changed_count,
            left=run.left_count,
            notifications_created=run.notifications_created,
            details_failed=run.details_failed,
            error=run.error,
        )

    # --- inbox ----------------------------------------------------------------------------------

    def items(self, user_id: int, query: ItemQuery) -> ItemPage:
        with self._uow_factory() as uow:
            account_id = self._account_id(uow)
            workflows = uow.workflows.list_for_account(account_id)
            by_key = {w.key: w for w in workflows}
            if query.workflow_keys:
                missing = [k for k in query.workflow_keys if k not in by_key]
                if missing:
                    raise WorkflowNotFoundError(missing[0])
                scope = [by_key[k].id for k in query.workflow_keys]
            else:
                scope = [
                    w.id
                    for w in workflows
                    if w.enabled and (query.category is None or w.category == query.category)
                ]
            records = uow.queries.inbox_records(user_id, workflow_ids=scope)

        now = self._clock()
        items = [self._item(r, now) for r in records]
        facets = FacetsView(
            portal_statuses=sorted({i.portal_status for i in items if i.portal_status}),
            procedures=sorted({i.procedure for i in items if i.procedure}),
            workflows=sorted({i.workflow_key for i in items}),
        )
        filtered = self._filter(items, query)
        ordered = self._sort(filtered, query)
        size = max(1, min(query.page_size, _MAX_PAGE_SIZE))
        page = max(1, query.page)
        window = ordered[(page - 1) * size : page * size]
        columns: list[ColumnView] = []
        if len(query.workflow_keys) == 1:
            definition = self._definition(query.workflow_keys[0])
            columns = self._columns(definition) if definition else []
        return ItemPage(
            items=window, total=len(ordered), page=page, page_size=size, facets=facets, columns=columns
        )

    @staticmethod
    def _filter(items: Sequence[ItemView], query: ItemQuery) -> list[ItemView]:
        result = list(items)
        if query.search:
            needle = query.search.strip().casefold()
            result = [
                i
                for i in result
                if needle
                in " ".join(
                    (i.dossier_number, i.insured_name, i.registration, i.garage)
                ).casefold()
            ]
        if query.unread_only:
            result = [i for i in result if i.unread]
        if query.unread_kind:
            wanted = query.unread_kind.upper()
            arrivals = {
                NotificationKind.WORKFLOW_ITEM_NEW.value,
                NotificationKind.WORKFLOW_ITEM_RETURNED.value,
                NotificationKind.NEW_AGREEMENT_DOSSIER.value,
            }
            if wanted == "ARRIVAL":
                result = [i for i in result if i.unread_kind in arrivals]
            elif wanted == "CHANGED":
                result = [i for i in result if i.unread_kind == NotificationKind.WORKFLOW_ITEM_CHANGED.value]
        if query.work_status is not None:
            result = [i for i in result if i.work_status is query.work_status]
        if query.portal_status:
            result = [i for i in result if i.portal_status == query.portal_status]
        if query.procedure:
            result = [i for i in result if i.procedure == query.procedure]
        return result

    @staticmethod
    def _sort(items: list[ItemView], query: ItemQuery) -> list[ItemView]:
        floor = datetime.min.replace(tzinfo=UTC)

        def date_key(i: ItemView) -> datetime:
            return i.primary_date or i.detected_at or floor

        keys: dict[str, Callable[[ItemView], object]] = {
            "date": date_key,
            "detected": lambda i: i.detected_at,
            "age": lambda i: i.queue_age_days,
            "number": lambda i: i.dossier_number.casefold(),
            "name": lambda i: i.insured_name.casefold(),
            "status": lambda i: _WORK_ORDER[i.work_status],
            "workflow": lambda i: (i.workflow_category, i.workflow_name),
        }
        if query.sort == "default":
            # Unread first, then the most recent primary date.
            return sorted(items, key=lambda i: (not i.unread, -date_key(i).timestamp()))
        key = keys.get(query.sort, date_key)
        return sorted(items, key=key, reverse=query.descending)  # type: ignore[arg-type]

    # --- dossier --------------------------------------------------------------------------------

    def search(self, needle: str) -> list[DossierHitView]:
        if len(needle.strip()) < 2:
            return []
        with self._uow_factory() as uow:
            hits = uow.queries.search_dossiers(needle)
            names = {}
            account_id = self._account_id(uow)
            for workflow in uow.workflows.list_for_account(account_id):
                names[workflow.key] = workflow.name
        return [
            DossierHitView(
                dossier_id=h.dossier_id,
                dossier_number=h.dossier_number,
                insured_name=h.insured_name,
                registration=h.registration,
                garage=h.garage,
                portal_status=h.portal_status,
                active=h.active,
                workflows=[names.get(k, k) for k in h.active_workflow_keys],
            )
            for h in hits
        ]

    def dossier(self, user_id: int, dossier_id: int) -> DossierView:
        with self._uow_factory() as uow:
            dossier = uow.dossiers.get(dossier_id)
            if dossier is None:
                raise DossierNotFoundError(dossier_id)
            records = uow.queries.inbox_records(user_id, dossier_id=dossier_id, active_only=False)
            occurrences = uow.workflow_occurrences.list_for_dossier(dossier_id)
            unread_ids = uow.queries.unread_occurrence_ids(user_id, dossier_id)
            events = uow.queries.events(limit=200, dossier_id=dossier_id)
            notes = uow.dossier_notes.list_for_dossier(dossier_id)
            users = {u.id: u.display_name for u in uow.users.list_all()}

        by_membership: dict[int, list[OccurrenceView]] = {}
        for occurrence in occurrences:
            assert occurrence.id is not None
            by_membership.setdefault(occurrence.membership_id, []).append(
                OccurrenceView(
                    id=occurrence.id,
                    number=occurrence.occurrence_number,
                    origin=occurrence.origin,
                    detected_at=occurrence.detected_at,
                    unread=occurrence.id in unread_ids,
                )
            )
        memberships = []
        names: dict[int, str] = {}
        for record in sorted(
            records,
            key=lambda r: (
                not r.active,
                (self._definition(r.workflow_key).sort_order if self._definition(r.workflow_key) else 999),
            ),
        ):
            definition = self._definition(record.workflow_key)
            primary, raw, _ = self._primary_date(definition, record)
            fields: list[FieldValue] = []
            if definition is not None:
                for spec in definition.list_fields:
                    if spec.key in COMMON_FIELD_KEYS:
                        continue
                    fields.append(
                        FieldValue(
                            spec.key, spec.label, record.captured_fields.get(spec.key, ""), spec.date
                        )
                    )
            names[record.membership_id] = record.workflow_name
            memberships.append(
                MembershipView(
                    membership_id=record.membership_id,
                    workflow_key=record.workflow_key,
                    workflow_name=record.workflow_name,
                    workflow_category=record.workflow_category,
                    notification_class=record.notification_class,
                    rules_status=record.rules_status,
                    active=record.active,
                    first_seen_at=record.first_seen_at,
                    last_seen_at=record.last_seen_at,
                    work_status=record.work_status,
                    work_version=record.work_version,
                    work_updated_at=record.work_updated_at,
                    work_updated_by=users.get(record.work_updated_by) if record.work_updated_by else None,
                    current_occurrence_id=record.occurrence_id,
                    occurrences=sorted(
                        by_membership.get(record.membership_id, []), key=lambda o: -o.number
                    ),
                    fields=fields,
                    primary_date=primary,
                    primary_date_raw=raw,
                    omegaflow_url=self._url(record.workflow_route, record.record_id),
                )
            )
        detail_labels = {spec.key: spec.label for spec in self._catalog.shared_detail_fields()}
        dates = [
            FieldValue(key, detail_labels.get(key, key), value, True)
            for key, value in dossier.detail_fields.items()
            if key in detail_labels and value.strip()
        ]
        return DossierView(
            dossier=DossierCommonView(
                id=dossier_id,
                record_id=dossier.record_id,
                dossier_number=dossier.dossier_number,
                insured_name=dossier.insured_name,
                procedure=dossier.procedure,
                registration=dossier.registration,
                garage=dossier.garage,
                portal_status=dossier.portal_status,
                city=dossier.city,
                observation_count=dossier.observation_count,
                estimate_amount_raw=dossier.estimate_amount_raw,
                active=dossier.active,
                first_seen_at=dossier.first_seen_at,
                last_seen_at=dossier.last_seen_at,
                detail_error=dossier.detail_error,
                dates=dates,
            ),
            memberships=memberships,
            events=[self._event_out(e) for e in events],
            notes=[self._note_view(n, users, names) for n in reversed(notes)],
        )

    @staticmethod
    def _note_view(note, users: dict[int, str], names: dict[int, str]) -> NoteView:
        return NoteView(
            id=note.id,
            body=note.body,
            author=users.get(note.author_id, "Utilisateur supprimé"),
            created_at=note.created_at,
            membership_id=note.workflow_membership_id,
            workflow_name=names.get(note.workflow_membership_id) if note.workflow_membership_id else None,
        )

    # --- mutations (all explicit, all per-employee or shared as documented) ----------------------

    def acknowledge_occurrence(self, user_id: int, occurrence_id: int) -> int:
        """Read one occurrence's alerts for this employee only. Idempotent."""
        with self._uow_factory() as uow:
            if uow.queries.occurrence_owner(occurrence_id) is None:
                raise OccurrenceNotFoundError(occurrence_id)
            count = uow.notifications.acknowledge_occurrence(occurrence_id, user_id, self._clock())
            uow.commit()
            return count

    def set_work_status(
        self, user_id: int, membership_id: int, status: WorkStatus, expected_version: int | None
    ) -> WorkStateView:
        with self._uow_factory() as uow:
            if uow.queries.membership_owner(membership_id) is None:
                raise MembershipNotFoundError(membership_id)
            work = uow.workflow_work.upsert(membership_id, status, expected_version, user_id, self._clock())
            user = uow.users.get(user_id)
            uow.commit()
            return WorkStateView(
                membership_id=membership_id,
                status=work.status,
                version=work.version,
                updated_at=work.updated_at,
                updated_by=user.display_name if user else None,
            )

    def current_work_state(self, membership_id: int) -> WorkStateView:
        with self._uow_factory() as uow:
            work = uow.workflow_work.get(membership_id)
            if work is None:
                raise MembershipNotFoundError(membership_id)
            user = uow.users.get(work.updated_by) if work.updated_by else None
            return WorkStateView(
                membership_id=membership_id,
                status=work.status,
                version=work.version,
                updated_at=work.updated_at,
                updated_by=user.display_name if user else None,
            )

    def notes(self, dossier_id: int) -> list[NoteView]:
        with self._uow_factory() as uow:
            if uow.dossiers.get(dossier_id) is None:
                raise DossierNotFoundError(dossier_id)
            users = {u.id: u.display_name for u in uow.users.list_all()}
            names = {
                r.membership_id: r.workflow_name
                for r in uow.queries.inbox_records(0, dossier_id=dossier_id, active_only=False)
            }
            notes = uow.dossier_notes.list_for_dossier(dossier_id)
        return [self._note_view(n, users, names) for n in reversed(notes)]

    def add_note(
        self, user_id: int, dossier_id: int, body: str, membership_id: int | None = None
    ) -> NoteView:
        body = body.strip()
        if len(body) > MAX_NOTE_LENGTH:
            raise NoteTooLongError(len(body), MAX_NOTE_LENGTH)
        if not body:
            raise EmptyNoteError()
        with self._uow_factory() as uow:
            if uow.dossiers.get(dossier_id) is None:
                raise DossierNotFoundError(dossier_id)
            workflow_name = None
            if membership_id is not None:
                owner = uow.queries.membership_owner(membership_id)
                if owner is None or owner[1] != dossier_id:
                    raise MembershipNotFoundError(membership_id)
                for record in uow.queries.inbox_records(user_id, dossier_id=dossier_id, active_only=False):
                    if record.membership_id == membership_id:
                        workflow_name = record.workflow_name
            note = uow.dossier_notes.add(dossier_id, user_id, body, self._clock(), membership_id)
            user = uow.users.get(user_id)
            uow.commit()
            return NoteView(
                id=note.id,
                body=note.body,
                author=user.display_name if user else "",
                created_at=note.created_at,
                membership_id=membership_id,
                workflow_name=workflow_name,
            )

    # --- synchronization health -------------------------------------------------------------------

    def request_sync(self) -> bool:
        """Ask the worker for an immediate cycle. Returns False when one is already queued."""
        with self._uow_factory() as uow:
            if uow.outbox.has_pending(OutboxTopic.SYNC_REQUESTED):
                return False
            uow.outbox.add(OutboxTopic.SYNC_REQUESTED, {}, self._clock())
            uow.commit()
            return True

    def sync_health(self, user_id: int, live_state: str | None = None) -> SyncHealthView:
        with self._uow_factory() as uow:
            account_id = self._account_id(uow)
            stats = uow.queries.workflow_stats(user_id, account_id)
            session = self._session_health(uow, live_state)
            runs = uow.sync_runs.recent(account_id, limit=15)
            alerts = uow.queries.events(limit=20, operational=True)
            pending_sync = uow.outbox.pending_count(OutboxTopic.SYNC_REQUESTED)
            pending_all = uow.outbox.pending_count()
        views = [v for v in (self._sync_run_view(r) for r in runs) if v is not None]
        return SyncHealthView(
            session=session,
            last_run=views[0] if views else None,
            recent_runs=views,
            workflows=[self._workflow_view(s) for s in stats],
            alerts=[self._event_out(e) for e in alerts],
            pending_sync_requests=pending_sync,
            outbox_pending=pending_all,
        )

    # --- administration ---------------------------------------------------------------------------

    def update_workflow(
        self,
        user_id: int,
        key: str,
        *,
        enabled: bool | None = None,
        rules_status: WorkflowRulesStatus | None = None,
        notification_class: NotificationClass | None = None,
    ) -> WorkflowView:
        with self._uow_factory() as uow:
            account_id = self._account_id(uow)
            workflow = uow.workflows.get_by_key(account_id, key)
            if workflow is None or workflow.id is None:
                raise WorkflowNotFoundError(key)
            uow.workflows.update_admin_config(
                workflow.id,
                enabled=enabled,
                rules_status=rules_status,
                notification_class=notification_class,
            )
            uow.commit()
        return next(w for w in self.workflows(user_id) if w.key == key)
