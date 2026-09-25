"""Typed output of the employee use cases (serialized as-is by the JSON API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rma_portal.domain.enums import (
    NotificationClass,
    OccurrenceOrigin,
    PollStatus,
    WorkflowRulesStatus,
    WorkStatus,
)


@dataclass(frozen=True, slots=True)
class LastRunView:
    status: PollStatus | None
    baseline: bool
    rows_seen: int
    created: int
    returned: int
    changed: int
    left: int
    details_failed: int


@dataclass(frozen=True, slots=True)
class WorkflowView:
    key: str
    name: str
    category: str
    route: str
    view_id: str
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus
    needs_site_validation: bool
    """True for CAPTURE_DERIVED rules: shown as "À valider sur site"."""

    enabled: bool
    baseline_completed_at: datetime | None
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_poll_status: PollStatus | None
    last_error: str | None
    active_count: int
    unread_new: int
    unread_changed: int
    by_work_status: dict[str, int]
    last_run: LastRunView | None = None


@dataclass(frozen=True, slots=True)
class ColumnView:
    key: str
    label: str
    kind: str
    date: bool


@dataclass(frozen=True, slots=True)
class PollRunView:
    id: int
    sync_run_id: int
    status: PollStatus
    started_at: datetime
    completed_at: datetime | None
    baseline: bool
    rows_seen: int
    pages_seen: int
    created: int
    returned: int
    changed: int
    left: int
    notifications_created: int
    details_failed: int
    error: str | None


@dataclass(frozen=True, slots=True)
class EventOut:
    id: int
    kind: str
    notification_class: NotificationClass
    detected_at: datetime
    workflow_key: str | None
    workflow_name: str | None
    dossier_id: int | None
    dossier_number: str | None
    insured_name: str | None
    occurrence_id: int | None
    membership_id: int | None
    changed_fields: list[str]
    changed_labels: list[str]
    message: str | None


@dataclass(frozen=True, slots=True)
class WorkflowDetailView:
    workflow: WorkflowView
    filter_label: str | None
    primary_date_labels: list[str]
    columns: list[ColumnView]
    recent_runs: list[PollRunView]
    recent_events: list[EventOut]


@dataclass(frozen=True, slots=True)
class ItemView:
    workflow_key: str
    workflow_name: str
    workflow_category: str
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus
    membership_id: int
    occurrence_id: int
    occurrence_number: int
    occurrence_origin: OccurrenceOrigin
    dossier_id: int
    record_id: str
    dossier_number: str
    insured_name: str
    registration: str
    garage: str
    procedure: str
    portal_status: str
    city: str
    active: bool
    detected_at: datetime
    primary_date: datetime | None
    primary_date_raw: str
    primary_date_label: str | None
    queue_age_days: int
    work_status: WorkStatus
    work_version: int
    unread: bool
    unread_kind: str | None
    fields: dict[str, str]
    omegaflow_url: str


@dataclass(frozen=True, slots=True)
class FacetsView:
    portal_statuses: list[str]
    procedures: list[str]
    workflows: list[str]


@dataclass(frozen=True, slots=True)
class ItemPage:
    items: list[ItemView]
    total: int
    page: int
    page_size: int
    facets: FacetsView
    columns: list[ColumnView] = field(default_factory=list)
    """The workflow-specific columns when the page is scoped to one workflow."""


@dataclass(frozen=True, slots=True)
class OccurrenceView:
    id: int
    number: int
    origin: OccurrenceOrigin
    detected_at: datetime
    unread: bool


@dataclass(frozen=True, slots=True)
class FieldValue:
    key: str
    label: str
    value: str
    date: bool = False


@dataclass(frozen=True, slots=True)
class MembershipView:
    membership_id: int
    workflow_key: str
    workflow_name: str
    workflow_category: str
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus
    active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    work_status: WorkStatus
    work_version: int
    work_updated_at: datetime | None
    work_updated_by: str | None
    current_occurrence_id: int
    occurrences: list[OccurrenceView]
    fields: list[FieldValue]
    primary_date: datetime | None
    primary_date_raw: str
    omegaflow_url: str


@dataclass(frozen=True, slots=True)
class NoteView:
    id: int
    body: str
    author: str
    created_at: datetime
    membership_id: int | None
    workflow_name: str | None


@dataclass(frozen=True, slots=True)
class DossierCommonView:
    id: int
    record_id: str
    dossier_number: str
    insured_name: str
    procedure: str
    registration: str
    garage: str
    portal_status: str
    city: str
    observation_count: str
    estimate_amount_raw: str
    active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    detail_error: str | None
    dates: list[FieldValue]


@dataclass(frozen=True, slots=True)
class DossierView:
    dossier: DossierCommonView
    memberships: list[MembershipView]
    events: list[EventOut]
    notes: list[NoteView]


@dataclass(frozen=True, slots=True)
class DossierHitView:
    dossier_id: int
    dossier_number: str
    insured_name: str
    registration: str
    garage: str
    portal_status: str
    active: bool
    workflows: list[str]


@dataclass(frozen=True, slots=True)
class WorkStateView:
    membership_id: int
    status: WorkStatus
    version: int
    updated_at: datetime
    updated_by: str | None


@dataclass(frozen=True, slots=True)
class SessionHealthView:
    state: str
    label: str
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    syncing: bool
    connect_url: str | None


@dataclass(frozen=True, slots=True)
class SyncRunView:
    id: int
    trigger: str
    status: PollStatus
    started_at: datetime
    completed_at: datetime | None
    workflows_total: int
    workflows_complete: int
    workflows_partial: int
    workflows_auth_required: int
    workflows_failed: int
    error: str | None


@dataclass(frozen=True, slots=True)
class SyncHealthView:
    session: SessionHealthView
    last_run: SyncRunView | None
    recent_runs: list[SyncRunView]
    workflows: list[WorkflowView]
    alerts: list[EventOut]
    pending_sync_requests: int
    outbox_pending: int


@dataclass(frozen=True, slots=True)
class CountersView:
    actionable_new: int
    changed_unread: int
    active_total: int
    problem_workflows: int
    by_work_status: dict[str, int]


@dataclass(frozen=True, slots=True)
class DashboardView:
    session: SessionHealthView
    last_run: SyncRunView | None
    counters: CountersView
    workflows: list[WorkflowView]
    activity: list[EventOut]
    alerts: list[EventOut]
