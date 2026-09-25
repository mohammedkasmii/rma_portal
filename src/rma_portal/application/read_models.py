"""Read models: flat, framework-free views the employee API is built from.

Everything here is derived from stored facts (memberships, occurrences, events,
work status); the API never recomputes business rules, it only presents them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from rma_portal.domain.enums import (
    NotificationClass,
    NotificationKind,
    OccurrenceOrigin,
    PollStatus,
    WorkflowRulesStatus,
    WorkStatus,
)


@dataclass(frozen=True, slots=True)
class InboxRecord:
    """One dossier's membership in one workflow, joined with everything a row needs."""

    workflow_id: int
    workflow_key: str
    workflow_name: str
    workflow_category: str
    workflow_route: str
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus

    membership_id: int
    dossier_id: int
    record_id: str
    active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime | None
    captured_fields: Mapping[str, str]

    occurrence_id: int
    occurrence_number: int
    occurrence_origin: OccurrenceOrigin
    occurrence_detected_at: datetime

    dossier_number: str
    insured_name: str
    procedure: str
    registration: str
    garage: str
    portal_status: str
    city: str
    details_href: str
    detail_fields: Mapping[str, str]

    work_status: WorkStatus
    work_version: int
    work_updated_at: datetime | None
    work_updated_by: int | None

    unread_counts: Mapping[NotificationKind, int] = field(default_factory=dict)

    @property
    def unread(self) -> bool:
        return bool(self.unread_counts)

    @property
    def unread_kind(self) -> NotificationKind | None:
        """The most important unread alert kind: arrivals before changes."""
        for kind in (
            NotificationKind.WORKFLOW_ITEM_RETURNED,
            NotificationKind.WORKFLOW_ITEM_NEW,
            NotificationKind.NEW_AGREEMENT_DOSSIER,
            NotificationKind.WORKFLOW_ITEM_CHANGED,
        ):
            if self.unread_counts.get(kind):
                return kind
        return None


@dataclass(frozen=True, slots=True)
class WorkflowStat:
    """Counts and health of one workflow for one employee."""

    workflow_id: int
    key: str
    name: str
    category: str
    route: str
    view_id: str
    sort_order: int
    enabled: bool
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus
    baseline_completed_at: datetime | None
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_poll_status: PollStatus | None
    last_error: str | None
    active_count: int
    unread_new: int
    unread_changed: int
    by_work_status: Mapping[WorkStatus, int]
    last_run_rows: int | None = None
    last_run_created: int | None = None
    last_run_returned: int | None = None
    last_run_changed: int | None = None
    last_run_left: int | None = None
    last_run_details_failed: int | None = None
    last_run_baseline: bool | None = None


@dataclass(frozen=True, slots=True)
class EventView:
    id: int
    kind: str
    notification_class: NotificationClass
    detected_at: datetime
    workflow_id: int | None
    workflow_key: str | None
    workflow_name: str | None
    dossier_id: int | None
    dossier_number: str | None
    insured_name: str | None
    occurrence_id: int | None
    membership_id: int | None
    changed_fields: tuple[str, ...]
    message: str | None


@dataclass(frozen=True, slots=True)
class DossierHit:
    dossier_id: int
    record_id: str
    dossier_number: str
    insured_name: str
    registration: str
    garage: str
    portal_status: str
    active: bool
    active_workflow_keys: tuple[str, ...]
