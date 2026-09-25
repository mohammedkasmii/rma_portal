"""Pure business entities.

No FastAPI, SQLAlchemy, Camoufox or filesystem imports are allowed in this
module. Entities are plain dataclasses; persistence mapping lives in
``infrastructure.db``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from rma_portal.domain.enums import (
    AiFeature,
    AiRunStatus,
    NotificationClass,
    NotificationKind,
    OccurrenceOrigin,
    PollStatus,
    Role,
    SessionStatus,
    SyncTrigger,
    WorkflowEventKind,
    WorkflowRulesStatus,
    WorkStatus,
)


@dataclass(slots=True)
class User:
    id: int | None
    username: str
    display_name: str
    password_hash: str
    role: Role
    active: bool
    created_at: datetime


@dataclass(slots=True)
class PortalAccount:
    id: int | None
    name: str
    base_url: str
    enabled: bool
    baseline_completed_at: datetime | None
    session_status: SessionStatus
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None


@dataclass(slots=True)
class Workflow:
    """One independently observed OmegaFlow queue or business stage."""

    id: int | None
    portal_account_id: int
    key: str
    name: str
    category: str
    route: str
    view_id: str
    enabled: bool
    sort_order: int
    rules_status: WorkflowRulesStatus
    baseline_completed_at: datetime | None
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    notification_class: NotificationClass = NotificationClass.ACTION
    catalog_version: int = 1
    filter_field: str | None = None
    filter_operator: str | None = None
    filter_value: str | None = None
    filter_label: str | None = None
    primary_date_key: str | None = None
    last_poll_status: PollStatus | None = None


@dataclass(slots=True)
class WorkflowMembership:
    """One dossier's lifecycle inside one workflow.

    A shared dossier can belong to several queues simultaneously. Arrival,
    absence and reappearance therefore live here instead of on the dossier
    once the multi-workflow synchronizer is activated.
    """

    id: int | None
    workflow_id: int
    dossier_id: int
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    missing_complete_polls: int
    occurrence_number: int
    captured_fields: Mapping[str, str]
    fingerprint: str
    last_changed_at: datetime | None


@dataclass(slots=True)
class DossierDates:
    """The five captured portal dates, each kept alongside its raw text.

    Parsing may fail (unexpected format, empty cell); the raw text is always
    retained so nothing is silently lost.
    """

    date_creation: datetime | None = None
    date_creation_raw: str = ""
    date_premiere_fin_prevue: datetime | None = None
    date_premiere_fin_prevue_raw: str = ""
    date_fin_travaux_prevue: datetime | None = None
    date_fin_travaux_prevue_raw: str = ""
    date_envoi_devis_garage: datetime | None = None
    date_envoi_devis_garage_raw: str = ""
    date_photos_avant: datetime | None = None
    date_photos_avant_raw: str = ""


@dataclass(slots=True)
class Dossier:
    id: int | None
    portal_account_id: int
    record_id: str
    dossier_number: str
    insured_name: str
    procedure: str
    registration: str
    garage: str
    estimate_amount_raw: str
    portal_status: str
    city: str
    observation_count: str
    agreement_login: str
    details_href: str
    dates: DossierDates
    detail_complete: bool
    detail_error: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    missing_complete_polls: int
    detail_fields: Mapping[str, str] = field(default_factory=dict)
    detail_fetched_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkflowOccurrence:
    """One immutable appearance of a dossier in a workflow queue.

    A reappearance after deactivation creates a *new* occurrence, so
    notification and acknowledgement history is never overwritten. The end
    of an occurrence is recorded by a ``WORKFLOW_ITEM_LEFT`` event, never by
    mutating this row.
    """

    id: int | None
    membership_id: int
    workflow_id: int
    dossier_id: int
    occurrence_number: int
    origin: OccurrenceOrigin
    detected_at: datetime


@dataclass(slots=True)
class Notification:
    id: int | None
    dossier_id: int
    kind: NotificationKind
    detected_at: datetime
    workflow_id: int | None = None
    workflow_occurrence_id: int | None = None
    workflow_event_id: int | None = None


@dataclass(slots=True)
class NotificationRead:
    notification_id: int
    user_id: int
    seen_at: datetime


@dataclass(slots=True)
class DossierWork:
    dossier_id: int
    status: WorkStatus
    version: int
    updated_by: int | None
    updated_at: datetime


@dataclass(slots=True)
class WorkflowWork:
    """Shared work status of one dossier inside one workflow membership."""

    membership_id: int
    status: WorkStatus
    version: int
    updated_by: int | None
    updated_at: datetime


@dataclass(slots=True)
class DossierNote:
    """Shared dossier-level note with optional workflow-membership context."""

    id: int | None
    dossier_id: int
    author_id: int
    body: str
    created_at: datetime
    workflow_membership_id: int | None = None


@dataclass(slots=True)
class PollRun:
    id: int | None
    portal_account_id: int
    started_at: datetime
    completed_at: datetime | None
    status: PollStatus
    rows_seen: int
    pages_seen: int
    details_failed: int
    error: str | None = field(default=None)


@dataclass(slots=True)
class SyncRun:
    """Parent record of one complete synchronization cycle."""

    id: int | None
    portal_account_id: int
    trigger: SyncTrigger
    started_at: datetime
    completed_at: datetime | None
    status: PollStatus
    workflows_total: int = 0
    workflows_complete: int = 0
    workflows_partial: int = 0
    workflows_auth_required: int = 0
    workflows_failed: int = 0
    error: str | None = None


@dataclass(slots=True)
class WorkflowPollRun:
    """Outcome of reading one workflow inside a :class:`SyncRun`."""

    id: int | None
    sync_run_id: int
    workflow_id: int
    started_at: datetime
    completed_at: datetime | None
    status: PollStatus
    baseline: bool = False
    rows_seen: int = 0
    pages_seen: int = 0
    details_failed: int = 0
    created_count: int = 0
    returned_count: int = 0
    changed_count: int = 0
    left_count: int = 0
    notifications_created: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    """Immutable activity-feed entry.

    Only field *names* and short fingerprints of changed values are kept,
    never raw customer payloads.
    """

    id: int | None
    kind: WorkflowEventKind
    detected_at: datetime
    notification_class: NotificationClass
    workflow_id: int | None = None
    membership_id: int | None = None
    occurrence_id: int | None = None
    dossier_id: int | None = None
    poll_run_id: int | None = None
    changed_fields: tuple[str, ...] = ()
    before_fingerprints: Mapping[str, str] = field(default_factory=dict)
    after_fingerprints: Mapping[str, str] = field(default_factory=dict)
    message: str | None = None


@dataclass(slots=True)
class OutboxMessage:
    id: int | None
    topic: str
    payload: Mapping[str, object]
    created_at: datetime
    available_at: datetime
    processed_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class AiRun:
    id: int | None
    feature: AiFeature
    subject_type: str
    subject_id: int | None
    model: str
    prompt_version: str
    context_hash: str
    status: AiRunStatus
    started_at: datetime
    duration_ms: int
    result: Mapping[str, object] | None = None
    error: str | None = None
    created_by: int | None = None


class WorkStatusConflict(Exception):
    """Raised when an optimistic-locking version does not match."""

    def __init__(self, dossier_id: int, expected_version: int, actual_version: int) -> None:
        super().__init__(
            f"dossier {dossier_id}: expected version {expected_version}, "
            f"actual version {actual_version}"
        )
        self.dossier_id = dossier_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class NoteTooLongError(Exception):
    def __init__(self, length: int, limit: int) -> None:
        super().__init__(f"note body has {length} characters, limit is {limit}")
        self.length = length
        self.limit = limit


class EmptyNoteError(Exception):
    pass


class CannotDisableSelfError(Exception):
    """An administrator may not disable their own active account."""


class DuplicateUsernameError(Exception):
    def __init__(self, username: str) -> None:
        super().__init__(f"username {username!r} already exists")
        self.username = username
