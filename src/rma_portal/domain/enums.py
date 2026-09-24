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
    NEW_AGREEMENT_DOSSIER = "NEW_AGREEMENT_DOSSIER"


class WorkflowRulesStatus(enum.StrEnum):
    """Whether employees have approved a workflow's business policy.

    A technical queue contract may be captured and stored before the agency
    has decided which event, date and completion condition should drive work.
    Keeping that distinction explicit prevents speculative alerts.
    """

    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"


MAX_MISSING_COMPLETE_POLLS = 2
"""Number of consecutive COMPLETE polls a dossier may be absent from before
it is deactivated (see domain.sync_rules.reconcile)."""

MAX_NOTE_LENGTH = 2000
