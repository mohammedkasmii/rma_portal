"""Protocol interfaces implemented by the infrastructure layer.

Adding a future direct-API reader, or a second RMA queue, means writing a
new ``PortalReader``/``PortalReaderFactory`` pair and registering it in
``bootstrap`` -- nothing in ``SyncAgreementQueue`` or the web layer changes.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from datetime import datetime
from typing import Protocol

from rma_portal.application.dto import (
    DashboardRow,
    DossierDetails,
    PortalDossierRef,
    QueueRow,
    QueueSnapshot,
)
from rma_portal.domain.enums import NotificationKind, PollStatus, WorkStatus
from rma_portal.domain.models import (
    Dossier,
    DossierNote,
    DossierWork,
    Notification,
    PollRun,
    PortalAccount,
    User,
)
from rma_portal.domain.sync_rules import ExistingDossierState


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class PortalReader(Protocol):
    async def read_agreement_queue(self) -> QueueSnapshot: ...

    async def read_dossier_details(self, dossier: PortalDossierRef) -> DossierDetails: ...


class PortalReaderFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[PortalReader]: ...


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


class DossierRepository(Protocol):
    def existing_state_by_account(
        self, account_id: int
    ) -> dict[str, ExistingDossierState]: ...

    def get_by_record_id(self, account_id: int, record_id: str) -> Dossier | None: ...

    def get(self, dossier_id: int) -> Dossier | None: ...

    def create_from_row(self, account_id: int, row: QueueRow, now: datetime) -> Dossier: ...

    def touch(self, account_id: int, row: QueueRow, now: datetime) -> Dossier: ...

    def reactivate(self, account_id: int, row: QueueRow, now: datetime) -> Dossier: ...

    def apply_absence_increment(self, account_id: int, record_id: str, count: int) -> None: ...

    def deactivate(self, account_id: int, record_id: str) -> None: ...

    def save_details(self, dossier_id: int, details: DossierDetails) -> None: ...

    def dossiers_needing_detail_retry(self, account_id: int) -> list[Dossier]: ...

    def list_for_dashboard(self, *, user_id: int) -> list[DashboardRow]: ...


class NotificationRepository(Protocol):
    def create(
        self, dossier_id: int, kind: NotificationKind, detected_at: datetime
    ) -> Notification: ...

    def mark_all_existing_as_read_for_user(self, user_id: int, seen_at: datetime) -> None: ...

    def acknowledge_dossier(self, dossier_id: int, user_id: int, seen_at: datetime) -> None: ...

    def unread_count_for_user(self, user_id: int) -> int: ...

    def is_unread_for_user(self, dossier_id: int, user_id: int) -> bool: ...


class PollRunRepository(Protocol):
    def start(self, account_id: int, started_at: datetime) -> PollRun: ...

    def finish(
        self,
        poll_run_id: int,
        *,
        status: PollStatus,
        completed_at: datetime,
        rows_seen: int,
        pages_seen: int,
        details_failed: int,
        error: str | None,
    ) -> PollRun: ...


class UserRepository(Protocol):
    def get_by_username(self, username: str) -> User | None: ...

    def get(self, user_id: int) -> User | None: ...

    def list_all(self) -> list[User]: ...

    def create(self, user: User) -> User: ...

    def set_active(self, user_id: int, active: bool) -> None: ...

    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...


class DossierWorkRepository(Protocol):
    def get(self, dossier_id: int) -> DossierWork | None: ...

    def upsert(
        self,
        dossier_id: int,
        status: WorkStatus,
        expected_version: int | None,
        updated_by: int,
        updated_at: datetime,
    ) -> DossierWork: ...


class DossierNoteRepository(Protocol):
    def add(self, dossier_id: int, author_id: int, body: str, created_at: datetime) -> DossierNote: ...

    def list_for_dossier(self, dossier_id: int) -> list[DossierNote]: ...


class UnitOfWork(Protocol):
    """One SQLite transaction boundary spanning every repository below."""

    portal_accounts: PortalAccountRepository
    dossiers: DossierRepository
    notifications: NotificationRepository
    poll_runs: PollRunRepository
    users: UserRepository
    dossier_work: DossierWorkRepository
    dossier_notes: DossierNoteRepository

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[UnitOfWork]: ...
