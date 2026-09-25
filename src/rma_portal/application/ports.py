"""Protocol interfaces implemented by the infrastructure layer.

Adding a future direct-API reader, or a second RMA queue, means writing a
new ``PortalReader``/``PortalReaderFactory`` pair and registering it in
``bootstrap`` -- nothing in ``SyncWorkflows`` or the web layer changes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from datetime import datetime
from typing import Protocol

from rma_portal.application.dto import (
    DashboardRow,
    DossierDetailValues,
    PortalDossierRef,
    WorkflowReadOutcome,
)
from rma_portal.domain.enums import (
    AiFeature,
    NotificationClass,
    NotificationKind,
    OccurrenceOrigin,
    PollStatus,
    SessionStatus,
    SyncTrigger,
    WorkflowRulesStatus,
    WorkStatus,
)
from rma_portal.domain.models import (
    AiRun,
    Dossier,
    DossierDates,
    DossierNote,
    Notification,
    OutboxMessage,
    PortalAccount,
    SyncRun,
    User,
    Workflow,
    WorkflowEvent,
    WorkflowMembership,
    WorkflowOccurrence,
    WorkflowPollRun,
    WorkflowWork,
)
from rma_portal.domain.sync_rules import ExistingDossierState
from rma_portal.domain.workflow_definition import FieldSpec, WorkflowDefinition


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class PortalReader(Protocol):
    """One authenticated browser session, reused for a whole synchronization cycle."""

    async def read_workflow(self, definition: WorkflowDefinition) -> WorkflowReadOutcome:
        """Read one workflow's queue. Never raises for a read problem: the
        outcome is COMPLETE, PARTIAL, AUTH_REQUIRED or FAILED, so one broken
        workflow can neither stop nor invalidate another."""
        ...

    async def read_dossier_detail_fields(
        self, dossier: PortalDossierRef, fields: Sequence[FieldSpec]
    ) -> DossierDetailValues:
        """Read the shared detail page once per record per session (cached).
        Raises ``DetailReadError`` (non-fatal) or ``PortalAuthRequiredError``."""
        ...

    async def verify_authenticated(self) -> None:
        """Raise ``PortalAuthRequiredError`` if the saved session is not
        authenticated; otherwise return. Must not read the queue or any
        dossier detail -- callers rely on this being fast."""
        ...


class PortalReaderFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[PortalReader]: ...

    def has_saved_session(self) -> bool:
        """True once a session has been captured (see
        ``infrastructure.portal.session_state``) -- a fresh installation
        with nothing saved yet will never authenticate, so callers use this
        to skip a browser sync attempt entirely rather than open the
        profile only to fail after waiting on a queue view that cannot
        appear."""
        ...


class PortalAccountRepository(Protocol):
    def get(self, account_id: int) -> PortalAccount | None: ...

    def get_default(self) -> PortalAccount | None: ...

    def mark_poll_started(self, account_id: int, started_at: datetime) -> None: ...

    def mark_poll_finished(
        self,
        account_id: int,
        *,
        status: PollStatus,
        polled_at: datetime,
        error: str | None,
    ) -> None: ...

    def mark_baseline_completed(self, account_id: int, completed_at: datetime) -> None: ...

    def mark_session_checked(
        self,
        account_id: int,
        *,
        status: SessionStatus,
        checked_at: datetime,
        error: str | None,
    ) -> None:
        """Record the outcome of a bounded authentication check (no poll_runs
        row -- unlike :meth:`mark_poll_finished`, no queue/dossier read
        happened). Never touches ``last_success_at``."""
        ...


class WorkflowRepository(Protocol):
    def get(self, workflow_id: int) -> Workflow | None: ...

    def get_by_key(self, account_id: int, key: str) -> Workflow | None: ...

    def list_for_account(self, account_id: int, *, enabled_only: bool = False) -> list[Workflow]: ...

    def create(self, workflow: Workflow) -> Workflow: ...

    def update_definition(self, workflow: Workflow) -> None:
        """Refresh catalog-owned fields; admin-owned and poll state are untouched."""
        ...

    def update_admin_config(
        self,
        workflow_id: int,
        *,
        enabled: bool | None = None,
        rules_status: WorkflowRulesStatus | None = None,
        notification_class: NotificationClass | None = None,
    ) -> Workflow: ...

    def mark_poll_finished(
        self, workflow_id: int, *, status: PollStatus, polled_at: datetime, error: str | None
    ) -> None: ...

    def mark_baseline_completed(self, workflow_id: int, completed_at: datetime) -> None: ...


class WorkflowMembershipRepository(Protocol):
    def get(self, workflow_id: int, dossier_id: int) -> WorkflowMembership | None: ...

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowMembership]: ...

    def get_by_id(self, membership_id: int) -> WorkflowMembership | None: ...

    def create(
        self,
        *,
        workflow_id: int,
        dossier_id: int,
        seen_at: datetime,
        captured_fields: Mapping[str, str],
        fingerprint: str,
    ) -> WorkflowMembership: ...

    def refresh(
        self,
        membership_id: int,
        *,
        seen_at: datetime,
        captured_fields: Mapping[str, str],
        fingerprint: str,
        changed: bool,
    ) -> None: ...

    def reactivate(
        self,
        membership_id: int,
        *,
        seen_at: datetime,
        captured_fields: Mapping[str, str],
        fingerprint: str,
    ) -> int:
        """Returns the new occurrence number."""
        ...

    def apply_absence_increment(self, membership_id: int, count: int) -> None: ...

    def deactivate(self, membership_id: int) -> None: ...

    def by_record_id(self, workflow_id: int) -> dict[str, WorkflowMembership]: ...

    def existing_state_by_workflow(
        self, workflow_id: int
    ) -> dict[str, ExistingDossierState]:
        """Return membership lifecycle keyed by the dossier's portal record id."""
        ...


class DossierRepository(Protocol):
    def get_by_record_id(self, account_id: int, record_id: str) -> Dossier | None: ...

    def get(self, dossier_id: int) -> Dossier | None: ...

    def upsert_from_workflow_row(
        self,
        account_id: int,
        record_id: str,
        details_href: str,
        common_values: Mapping[str, str],
        now: datetime,
    ) -> tuple[Dossier, bool, bool]:
        """Create or refresh the shared dossier a queue row belongs to.

        Only the common columns the row's view renders are written. Returns
        ``(dossier, created, portal_status_changed)``.
        """
        ...

    def set_active(self, dossier_id: int, active: bool) -> None: ...

    def save_detail_values(
        self,
        dossier_id: int,
        values: Mapping[str, str],
        dates: DossierDates | None,
        fetched_at: datetime,
    ) -> None: ...

    def mark_detail_failed(self, dossier_id: int, error: str, attempted_at: datetime) -> None: ...

    def list_for_dashboard(
        self, *, user_id: int, workflow_key: str = "agreement_garage"
    ) -> list[DashboardRow]:
        """The pre-V2 (single-queue) dashboard: the active members of one workflow."""
        ...


class NotificationRepository(Protocol):
    def create_for_occurrence(
        self,
        *,
        dossier_id: int,
        workflow_id: int,
        occurrence_id: int,
        kind: NotificationKind,
        detected_at: datetime,
        event_id: int | None = None,
    ) -> Notification: ...

    def acknowledge_occurrence(self, occurrence_id: int, user_id: int, seen_at: datetime) -> int:
        """Read one occurrence's alerts for one employee only; returns how many."""
        ...

    def unread_counts_for_user(
        self, user_id: int
    ) -> dict[tuple[int | None, NotificationKind], int]: ...

    def is_occurrence_unread_for_user(self, occurrence_id: int, user_id: int) -> bool: ...

    def mark_all_existing_as_read_for_user(self, user_id: int, seen_at: datetime) -> None: ...


class UserRepository(Protocol):
    def get_by_username(self, username: str) -> User | None: ...

    def get(self, user_id: int) -> User | None: ...

    def list_all(self) -> list[User]: ...

    def create(self, user: User) -> User: ...

    def set_active(self, user_id: int, active: bool) -> None: ...

    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...


class DossierNoteRepository(Protocol):
    def add(
        self,
        dossier_id: int,
        author_id: int,
        body: str,
        created_at: datetime,
        workflow_membership_id: int | None = None,
    ) -> DossierNote: ...

    def list_for_dossier(self, dossier_id: int) -> list[DossierNote]: ...


class WorkflowCatalog(Protocol):
    """The code-defined set of observable OmegaFlow workflows."""

    def definitions(self) -> tuple[WorkflowDefinition, ...]: ...

    def get(self, key: str) -> WorkflowDefinition | None: ...

    def shared_detail_fields(self) -> tuple[FieldSpec, ...]: ...


class WorkflowOccurrenceRepository(Protocol):
    """Insert-only: occurrences are immutable."""

    def create(
        self,
        *,
        membership_id: int,
        workflow_id: int,
        dossier_id: int,
        occurrence_number: int,
        origin: OccurrenceOrigin,
        detected_at: datetime,
    ) -> WorkflowOccurrence: ...

    def get(self, occurrence_id: int) -> WorkflowOccurrence | None: ...

    def list_for_membership(self, membership_id: int) -> list[WorkflowOccurrence]: ...

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowOccurrence]: ...

    def latest_for_membership(self, membership_id: int) -> WorkflowOccurrence | None: ...


class WorkflowWorkRepository(Protocol):
    def get(self, membership_id: int) -> WorkflowWork | None: ...

    def create_initial(self, membership_id: int, now: datetime) -> WorkflowWork: ...

    def upsert(
        self,
        membership_id: int,
        status: WorkStatus,
        expected_version: int | None,
        updated_by: int,
        updated_at: datetime,
    ) -> WorkflowWork: ...

    def counts_by_workflow(
        self, workflow_ids: list[int]
    ) -> dict[tuple[int, WorkStatus], int]: ...


class SyncRunRepository(Protocol):
    def start(self, account_id: int, trigger: SyncTrigger, started_at: datetime) -> SyncRun: ...

    def finish(
        self,
        sync_run_id: int,
        *,
        status: PollStatus,
        completed_at: datetime,
        workflows_total: int,
        workflows_complete: int,
        workflows_partial: int,
        workflows_auth_required: int,
        workflows_failed: int,
        error: str | None,
    ) -> SyncRun: ...

    def latest(self, account_id: int) -> SyncRun | None: ...

    def recent(self, account_id: int, limit: int = 20) -> list[SyncRun]: ...


class WorkflowPollRunRepository(Protocol):
    def start(
        self, sync_run_id: int, workflow_id: int, started_at: datetime, *, baseline: bool
    ) -> WorkflowPollRun: ...

    def finish(
        self,
        poll_run_id: int,
        *,
        status: PollStatus,
        completed_at: datetime,
        rows_seen: int,
        pages_seen: int,
        details_failed: int,
        created_count: int,
        returned_count: int,
        changed_count: int,
        left_count: int,
        notifications_created: int,
        error: str | None,
        baseline: bool | None = None,
    ) -> WorkflowPollRun: ...

    def list_for_sync_run(self, sync_run_id: int) -> list[WorkflowPollRun]: ...

    def latest_for_workflow(self, workflow_id: int) -> WorkflowPollRun | None: ...

    def recent_for_workflow(self, workflow_id: int, limit: int = 10) -> list[WorkflowPollRun]: ...


class WorkflowEventRepository(Protocol):
    """Insert-only activity feed."""

    def add(self, event: WorkflowEvent) -> WorkflowEvent: ...

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowEvent]: ...

    def list_recent(
        self,
        *,
        limit: int = 50,
        workflow_id: int | None = None,
        classes: tuple[NotificationClass, ...] | None = None,
        only_operational: bool = False,
    ) -> list[WorkflowEvent]: ...

    def latest_operational_for_workflow(self, workflow_id: int | None) -> WorkflowEvent | None: ...


class OutboxRepository(Protocol):
    def add(
        self,
        topic: str,
        payload: Mapping[str, object],
        now: datetime,
        *,
        available_at: datetime | None = None,
    ) -> OutboxMessage: ...

    def claim_pending(self, now: datetime, limit: int = 50) -> list[OutboxMessage]: ...

    def mark_processed(self, message_id: int, processed_at: datetime) -> None: ...

    def mark_failed(self, message_id: int, error: str, now: datetime) -> None: ...

    def pending_count(self, topic: str | None = None) -> int: ...

    def has_pending(self, topic: str) -> bool: ...


class AiRunRepository(Protocol):
    def add(self, run: AiRun) -> AiRun: ...

    def latest_successful(
        self, feature: AiFeature, subject_type: str, subject_id: int | None
    ) -> AiRun | None: ...

    def recent(self, limit: int = 20) -> list[AiRun]: ...


class CycleLock(Protocol):
    """Guarantees that only one synchronization cycle runs across every process.

    ``try_acquire`` yields ``True`` when this caller now owns the cycle and
    ``False`` when another process already does; the lock is released when
    the context exits (or the holder's database session dies).
    """

    def try_acquire(self) -> AbstractContextManager[bool]: ...


class UnitOfWork(Protocol):
    """One SQLite transaction boundary spanning every repository below."""

    portal_accounts: PortalAccountRepository
    workflows: WorkflowRepository
    workflow_memberships: WorkflowMembershipRepository
    workflow_occurrences: WorkflowOccurrenceRepository
    workflow_work: WorkflowWorkRepository
    sync_runs: SyncRunRepository
    workflow_poll_runs: WorkflowPollRunRepository
    workflow_events: WorkflowEventRepository
    outbox: OutboxRepository
    ai_runs: AiRunRepository
    dossiers: DossierRepository
    notifications: NotificationRepository
    users: UserRepository
    dossier_notes: DossierNoteRepository

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[UnitOfWork]: ...
