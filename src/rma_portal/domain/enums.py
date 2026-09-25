from __future__ import annotations

import enum


class Role(enum.StrEnum):
    ADMIN = "ADMIN"
    EMPLOYEE = "EMPLOYEE"


class SessionStatus(enum.StrEnum):
    UNKNOWN = "UNKNOWN"
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"


class WorkStatus(enum.StrEnum):
    TO_DO = "TO_DO"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    DONE = "DONE"


class PollStatus(enum.StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FAILED = "FAILED"


class NotificationKind(enum.StrEnum):
    """Employee-facing alert kinds.

    ``NEW_AGREEMENT_DOSSIER`` is the pre-V2 Garage agréé kind. The V2
    migration rewrites existing rows to ``WORKFLOW_ITEM_NEW``; the member is
    kept so a database that was not migrated yet still loads.
    """

    NEW_AGREEMENT_DOSSIER = "NEW_AGREEMENT_DOSSIER"
    WORKFLOW_ITEM_NEW = "WORKFLOW_ITEM_NEW"
    WORKFLOW_ITEM_RETURNED = "WORKFLOW_ITEM_RETURNED"
    WORKFLOW_ITEM_CHANGED = "WORKFLOW_ITEM_CHANGED"


class NotificationClass(enum.StrEnum):
    """How a workflow's activity reaches employees.

    ``ACTION`` events can create unread alerts; ``INFORMATIONAL`` events are
    only shown in history/summaries; ``SILENT`` workflows are synchronized
    for search and detail context and emit no events.
    """

    ACTION = "ACTION"
    INFORMATIONAL = "INFORMATIONAL"
    SILENT = "SILENT"


class WorkflowEventKind(enum.StrEnum):
    WORKFLOW_ITEM_NEW = "WORKFLOW_ITEM_NEW"
    WORKFLOW_ITEM_RETURNED = "WORKFLOW_ITEM_RETURNED"
    WORKFLOW_ITEM_CHANGED = "WORKFLOW_ITEM_CHANGED"
    WORKFLOW_ITEM_COMPLETED = "WORKFLOW_ITEM_COMPLETED"
    WORKFLOW_ITEM_TRANSITION = "WORKFLOW_ITEM_TRANSITION"
    WORKFLOW_ITEM_LEFT = "WORKFLOW_ITEM_LEFT"
    SESSION_AUTH_REQUIRED = "SESSION_AUTH_REQUIRED"
    WORKFLOW_POLL_PARTIAL = "WORKFLOW_POLL_PARTIAL"
    WORKFLOW_POLL_FAILED = "WORKFLOW_POLL_FAILED"


OPERATIONAL_EVENT_KINDS = frozenset(
    {
        WorkflowEventKind.SESSION_AUTH_REQUIRED,
        WorkflowEventKind.WORKFLOW_POLL_PARTIAL,
        WorkflowEventKind.WORKFLOW_POLL_FAILED,
    }
)
"""Administrator-only alerts; they never touch dossier membership."""


class OccurrenceOrigin(enum.StrEnum):
    BASELINE = "BASELINE"
    NEW = "NEW"
    RETURNED = "RETURNED"


class SyncTrigger(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    CONNECT = "CONNECT"
    IMPORT = "IMPORT"


class OutboxTopic(enum.StrEnum):
    NOTIFICATION_CREATED = "notification.created"
    WORKFLOW_EVENT_RECORDED = "workflow.event.recorded"
    SYNC_REQUESTED = "sync.requested"
    AI_JOB_REQUESTED = "ai.job.requested"


class AiFeature(enum.StrEnum):
    DAILY_SUMMARY = "DAILY_SUMMARY"
    DOSSIER_SUMMARY = "DOSSIER_SUMMARY"
    HIGHLIGHT_EXPLANATION = "HIGHLIGHT_EXPLANATION"
    PRIORITY_SUGGESTION = "PRIORITY_SUGGESTION"
    ANOMALY_GROUPING = "ANOMALY_GROUPING"


class AiRunStatus(enum.StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INVALID_OUTPUT = "INVALID_OUTPUT"


class WorkflowRulesStatus(enum.StrEnum):
    """Whether employees have approved a workflow's business policy.

    ``UNCONFIRMED`` workflows are catalogued but inactive. ``CAPTURE_DERIVED``
    workflows run with the conservative queue-membership policy derived from
    the ScriptScrap captures and are shown as "À valider sur site" until the
    agency validates them. ``CONFIRMED`` workflows were validated by the
    agency. Promoting a rule never changes the event engine.
    """

    UNCONFIRMED = "UNCONFIRMED"
    CAPTURE_DERIVED = "CAPTURE_DERIVED"
    CONFIRMED = "CONFIRMED"


MAX_MISSING_COMPLETE_POLLS = 2
"""Number of consecutive COMPLETE polls a dossier may be absent from before
it is deactivated (see domain.sync_rules.reconcile)."""

MAX_NOTE_LENGTH = 2000
