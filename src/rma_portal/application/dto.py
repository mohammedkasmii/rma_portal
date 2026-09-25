"""Data transfer objects exchanged across the PortalReader boundary.

These are plain, framework-free dataclasses: the domain layer's
``sync_rules`` only needs record IDs and lifecycle state, while the row and
detail payloads defined here are consumed by the application layer when
persisting dossiers and workflow memberships.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from rma_portal.domain.enums import PollStatus, WorkStatus


@dataclass(frozen=True, slots=True)
class PortalDossierRef:
    """Enough information to fetch a dossier's detail page."""

    record_id: str
    details_href: str


@dataclass(frozen=True, slots=True)
class WorkflowQueueRow:
    """One row of any captured queue: identity, detail link and the captured
    list fields keyed by the workflow definition's field keys."""

    record_id: str
    details_href: str
    fields: Mapping[str, str]

    def as_ref(self) -> PortalDossierRef:
        return PortalDossierRef(record_id=self.record_id, details_href=self.details_href)


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    workflow_key: str
    rows: tuple[WorkflowQueueRow, ...] = field(default_factory=tuple)
    pages_seen: int = 0

    @property
    def rows_seen(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class WorkflowReadOutcome:
    """Independent result of reading one workflow.

    A failed workflow is data, not an exception: the caller keeps reading the
    remaining workflows and only reconciles the ones that produced rows.
    """

    workflow_key: str
    status: PollStatus
    snapshot: WorkflowSnapshot
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DossierDetailValues:
    """Raw text of the shared-detail fields present on a dossier detail page."""

    values: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DashboardRow:
    """One dashboard table row, already joined with work status/unread state."""

    dossier_id: int
    dossier_number: str
    insured_name: str
    registration: str
    garage: str
    portal_status: str
    date_envoi_devis_garage: datetime | None
    date_envoi_devis_garage_raw: str
    date_fin_travaux_prevue: datetime | None
    date_fin_travaux_prevue_raw: str
    detection_time: datetime
    work_status: WorkStatus
    unread: bool


class PortalAuthRequiredError(Exception):
    """The login form or session-validation screen was detected."""


class PortalReadError(Exception):
    """The poll could not produce any usable data (the workflow's poll outcome is FAILED)."""


class DetailReadError(Exception):
    """A single dossier's detail page could not be read (non-fatal)."""


class BrowserProfileLockedError(Exception):
    """The persistent Camoufox profile is held by session configuration.

    Raised by ``PortalReaderFactory.open()`` / its context manager's
    ``__aenter__`` when Configurer_Session_RMA.bat currently owns the
    cross-process profile lock. The caller must skip this poll safely.
    """


class BrowserTeardownError(Exception):
    """A browser's teardown (close) did not complete within its bound.

    Raised by ``session_setup.launch_visible_browser_and_wait`` when its own
    bounded ``AsyncCamoufox`` close does not finish -- the caller must treat
    the profile lock as unconfirmed rather than releasing it (see
    ``infrastructure.portal.profile_lock.mark_profile_teardown_unconfirmed``),
    since a fresh launch could otherwise race a browser process that might
    still be running against the same profile.
    """


@dataclass(frozen=True, slots=True)
class WorkflowSyncResult:
    """What one workflow's read-and-reconcile did inside a synchronization cycle."""

    workflow_key: str
    status: PollStatus
    rows_seen: int = 0
    pages_seen: int = 0
    baseline: bool = False
    created: int = 0
    returned: int = 0
    changed: int = 0
    left: int = 0
    notifications_created: int = 0
    details_failed: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Outcome of one full synchronization cycle (the aggregate of its workflows)."""

    status: PollStatus | None = None
    rows_seen: int = 0
    pages_seen: int = 0
    details_failed: int = 0
    error: str | None = None
    created: int = 0
    reactivated: int = 0
    deactivated: int = 0
    changed: int = 0
    notifications_created: int = 0
    skipped: bool = False
    skip_reason: str | None = None
    sync_run_id: int | None = None
    workflows: tuple[WorkflowSyncResult, ...] = ()
