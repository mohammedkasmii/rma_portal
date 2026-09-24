"""SQLAlchemy implementations of the ``application.ports`` protocols."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rma_portal.application.dto import DashboardRow, DossierDetails, QueueRow
from rma_portal.domain.enums import NotificationKind, PollStatus, SessionStatus, WorkStatus
from rma_portal.domain.models import (
    Dossier,
    DossierDates,
    DossierNote,
    DossierWork,
    Notification,
    PollRun,
    PortalAccount,
    User,
    Workflow,
    WorkflowMembership,
    WorkStatusConflict,
)
from rma_portal.domain.sync_rules import ExistingDossierState
from rma_portal.infrastructure.db.models import (
    DossierNoteRow,
    DossierRow,
    DossierWorkRow,
    NotificationReadRow,
    NotificationRow,
    PollRunRow,
    PortalAccountRow,
    UserRow,
    WorkflowMembershipRow,
    WorkflowRow,
)


def _dossier_dates(row: DossierRow) -> DossierDates:
    return DossierDates(
        date_creation=row.date_creation,
        date_creation_raw=row.date_creation_raw,
        date_premiere_fin_prevue=row.date_premiere_fin_prevue,
        date_premiere_fin_prevue_raw=row.date_premiere_fin_prevue_raw,
        date_fin_travaux_prevue=row.date_fin_travaux_prevue,
        date_fin_travaux_prevue_raw=row.date_fin_travaux_prevue_raw,
        date_envoi_devis_garage=row.date_envoi_devis_garage,
        date_envoi_devis_garage_raw=row.date_envoi_devis_garage_raw,
        date_photos_avant=row.date_photos_avant,
        date_photos_avant_raw=row.date_photos_avant_raw,
    )


def _to_domain_dossier(row: DossierRow) -> Dossier:
    return Dossier(
        id=row.id,
        portal_account_id=row.portal_account_id,
        record_id=row.record_id,
        dossier_number=row.dossier_number,
        insured_name=row.insured_name,
        procedure=row.procedure,
        registration=row.registration,
        garage=row.garage,
        estimate_amount_raw=row.estimate_amount_raw,
        portal_status=row.portal_status,
        city=row.city,
        observation_count=row.observation_count,
        agreement_login=row.agreement_login,
        details_href=row.details_href,
        dates=_dossier_dates(row),
        detail_complete=row.detail_complete,
        detail_error=row.detail_error,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        active=row.active,
        missing_complete_polls=row.missing_complete_polls,
    )


def _to_domain_user(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        display_name=row.display_name,
        password_hash=row.password_hash,
        role=row.role,
        active=row.active,
        created_at=row.created_at,
    )


def _to_domain_portal_account(row: PortalAccountRow) -> PortalAccount:
    return PortalAccount(
        id=row.id,
        name=row.name,
        base_url=row.base_url,
        enabled=row.enabled,
        baseline_completed_at=row.baseline_completed_at,
        session_status=row.session_status,
        last_poll_at=row.last_poll_at,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
    )


def _to_domain_workflow(row: WorkflowRow) -> Workflow:
    return Workflow(
        id=row.id,
        portal_account_id=row.portal_account_id,
        key=row.key,
        name=row.name,
        category=row.category,
        route=row.route,
        view_id=row.view_id,
        enabled=row.enabled,
        sort_order=row.sort_order,
        rules_status=row.rules_status,
        baseline_completed_at=row.baseline_completed_at,
        last_poll_at=row.last_poll_at,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
    )


def _to_domain_membership(row: WorkflowMembershipRow) -> WorkflowMembership:
    return WorkflowMembership(
        id=row.id,
        workflow_id=row.workflow_id,
        dossier_id=row.dossier_id,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        active=row.active,
        missing_complete_polls=row.missing_complete_polls,
        occurrence_number=row.occurrence_number,
        captured_fields=json.loads(row.captured_fields_json),
        fingerprint=row.fingerprint,
        last_changed_at=row.last_changed_at,
    )


def _to_domain_poll_run(row: PollRunRow) -> PollRun:
    return PollRun(
        id=row.id,
        portal_account_id=row.portal_account_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        rows_seen=row.rows_seen,
        pages_seen=row.pages_seen,
        details_failed=row.details_failed,
        error=row.error,
    )


def _to_domain_work(row: DossierWorkRow) -> DossierWork:
    return DossierWork(
        dossier_id=row.dossier_id,
        status=row.status,
        version=row.version,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _to_domain_note(row: DossierNoteRow) -> DossierNote:
    return DossierNote(
        id=row.id,
        dossier_id=row.dossier_id,
        author_id=row.author_id,
        body=row.body,
        created_at=row.created_at,
    )


def _apply_row_fields(orm_row: DossierRow, row: QueueRow) -> None:
    orm_row.dossier_number = row.dossier_number
    orm_row.insured_name = row.insured_name
    orm_row.procedure = row.procedure
    orm_row.registration = row.registration
    orm_row.garage = row.garage
    orm_row.estimate_amount_raw = row.estimate_amount_raw
    orm_row.portal_status = row.portal_status
    orm_row.city = row.city
    orm_row.observation_count = row.observation_count
    orm_row.agreement_login = row.agreement_login
    orm_row.details_href = row.details_href


class SqlAlchemyPortalAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, account_id: int) -> PortalAccount | None:
        row = self._session.get(PortalAccountRow, account_id)
        return _to_domain_portal_account(row) if row else None

    def get_default(self) -> PortalAccount | None:
        row = self._session.execute(
            select(PortalAccountRow).order_by(PortalAccountRow.id).limit(1)
        ).scalar_one_or_none()
        return _to_domain_portal_account(row) if row else None

    def mark_poll_started(self, account_id: int, started_at: datetime) -> None:
        row = self._session.get(PortalAccountRow, account_id)
        if row is not None:
            row.last_poll_at = started_at

    def mark_poll_finished(
        self,
        account_id: int,
        *,
        status: PollStatus,
        polled_at: datetime,
        error: str | None,
    ) -> None:
        row = self._session.get(PortalAccountRow, account_id)
        if row is None:
            return
        row.last_poll_at = polled_at
        row.last_error = error
        if status is PollStatus.COMPLETE:
            row.last_success_at = polled_at
        row.session_status = {
            PollStatus.COMPLETE: SessionStatus.READY,
            PollStatus.PARTIAL: SessionStatus.READY,
            PollStatus.AUTH_REQUIRED: SessionStatus.AUTH_REQUIRED,
            PollStatus.FAILED: SessionStatus.ERROR,
        }[status]

    def mark_baseline_completed(self, account_id: int, completed_at: datetime) -> None:
        row = self._session.get(PortalAccountRow, account_id)
        if row is not None and row.baseline_completed_at is None:
            row.baseline_completed_at = completed_at

    def mark_session_checked(
        self,
        account_id: int,
        *,
        status: SessionStatus,
        checked_at: datetime,
        error: str | None,
    ) -> None:
        row = self._session.get(PortalAccountRow, account_id)
        if row is None:
            return
        row.last_poll_at = checked_at
        row.last_error = error
        row.session_status = status


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workflow_id: int) -> Workflow | None:
        row = self._session.get(WorkflowRow, workflow_id)
        return _to_domain_workflow(row) if row else None

    def get_by_key(self, account_id: int, key: str) -> Workflow | None:
        row = self._session.execute(
            select(WorkflowRow).where(
                WorkflowRow.portal_account_id == account_id,
                WorkflowRow.key == key,
            )
        ).scalar_one_or_none()
        return _to_domain_workflow(row) if row else None

    def list_for_account(self, account_id: int, *, enabled_only: bool = False) -> list[Workflow]:
        statement = select(WorkflowRow).where(WorkflowRow.portal_account_id == account_id)
        if enabled_only:
            statement = statement.where(WorkflowRow.enabled.is_(True))
        rows = self._session.execute(
            statement.order_by(WorkflowRow.sort_order, WorkflowRow.id)
        ).scalars()
        return [_to_domain_workflow(row) for row in rows]


class SqlAlchemyWorkflowMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workflow_id: int, dossier_id: int) -> WorkflowMembership | None:
        row = self._session.execute(
            select(WorkflowMembershipRow).where(
                WorkflowMembershipRow.workflow_id == workflow_id,
                WorkflowMembershipRow.dossier_id == dossier_id,
            )
        ).scalar_one_or_none()
        return _to_domain_membership(row) if row else None

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowMembership]:
        rows = self._session.execute(
            select(WorkflowMembershipRow)
            .join(WorkflowRow, WorkflowRow.id == WorkflowMembershipRow.workflow_id)
            .where(WorkflowMembershipRow.dossier_id == dossier_id)
            .order_by(WorkflowRow.sort_order, WorkflowMembershipRow.id)
        ).scalars()
        return [_to_domain_membership(row) for row in rows]

    def existing_state_by_workflow(
        self, workflow_id: int
    ) -> dict[str, ExistingDossierState]:
        rows = self._session.execute(
            select(WorkflowMembershipRow, DossierRow.record_id)
            .join(DossierRow, DossierRow.id == WorkflowMembershipRow.dossier_id)
            .where(WorkflowMembershipRow.workflow_id == workflow_id)
        ).all()
        return {
            record_id: ExistingDossierState(
                record_id=record_id,
                active=membership.active,
                missing_complete_polls=membership.missing_complete_polls,
            )
            for membership, record_id in rows
        }


class SqlAlchemyDossierRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_row(self, account_id: int, record_id: str) -> DossierRow | None:
        return self._session.execute(
            select(DossierRow).where(
                DossierRow.portal_account_id == account_id,
                DossierRow.record_id == record_id,
            )
        ).scalar_one_or_none()

    def existing_state_by_account(self, account_id: int) -> dict[str, ExistingDossierState]:
        rows = self._session.execute(
            select(DossierRow.record_id, DossierRow.active, DossierRow.missing_complete_polls).where(
                DossierRow.portal_account_id == account_id
            )
        ).all()
        return {
            record_id: ExistingDossierState(record_id, active, missing)
            for record_id, active, missing in rows
        }

    def get_by_record_id(self, account_id: int, record_id: str) -> Dossier | None:
        row = self._get_row(account_id, record_id)
        return _to_domain_dossier(row) if row else None

    def get(self, dossier_id: int) -> Dossier | None:
        row = self._session.get(DossierRow, dossier_id)
        return _to_domain_dossier(row) if row else None

    def create_from_row(self, account_id: int, row: QueueRow, now: datetime) -> Dossier:
        orm_row = DossierRow(
            portal_account_id=account_id,
            record_id=row.record_id,
            first_seen_at=now,
            last_seen_at=now,
            active=True,
            missing_complete_polls=0,
            detail_complete=False,
        )
        _apply_row_fields(orm_row, row)
        self._session.add(orm_row)
        self._session.flush()
        self._session.add(
            DossierWorkRow(
                dossier_id=orm_row.id,
                status=WorkStatus.TO_DO,
                version=1,
                updated_by=None,
                updated_at=now,
            )
        )
        return _to_domain_dossier(orm_row)

    def touch(self, account_id: int, row: QueueRow, now: datetime) -> tuple[Dossier, bool]:
        orm_row = self._get_row(account_id, row.record_id)
        if orm_row is None:
            raise LookupError(f"dossier {row.record_id} not found for account {account_id}")
        status_changed = orm_row.portal_status != row.portal_status
        _apply_row_fields(orm_row, row)
        orm_row.last_seen_at = now
        orm_row.missing_complete_polls = 0
        return _to_domain_dossier(orm_row), status_changed

    def reactivate(self, account_id: int, row: QueueRow, now: datetime) -> Dossier:
        orm_row = self._get_row(account_id, row.record_id)
        if orm_row is None:
            raise LookupError(f"dossier {row.record_id} not found for account {account_id}")
        _apply_row_fields(orm_row, row)
        orm_row.active = True
        orm_row.missing_complete_polls = 0
        orm_row.last_seen_at = now
        orm_row.detail_complete = False
        orm_row.detail_error = None
        return _to_domain_dossier(orm_row)

    def apply_absence_increment(self, account_id: int, record_id: str, count: int) -> None:
        orm_row = self._get_row(account_id, record_id)
        if orm_row is not None:
            orm_row.missing_complete_polls = count

    def deactivate(self, account_id: int, record_id: str) -> None:
        orm_row = self._get_row(account_id, record_id)
        if orm_row is not None:
            orm_row.active = False

    def save_details(self, dossier_id: int, details: DossierDetails) -> None:
        orm_row = self._session.get(DossierRow, dossier_id)
        if orm_row is None:
            return
        dates = details.dates
        orm_row.date_creation = dates.date_creation
        orm_row.date_creation_raw = dates.date_creation_raw
        orm_row.date_premiere_fin_prevue = dates.date_premiere_fin_prevue
        orm_row.date_premiere_fin_prevue_raw = dates.date_premiere_fin_prevue_raw
        orm_row.date_fin_travaux_prevue = dates.date_fin_travaux_prevue
        orm_row.date_fin_travaux_prevue_raw = dates.date_fin_travaux_prevue_raw
        orm_row.date_envoi_devis_garage = dates.date_envoi_devis_garage
        orm_row.date_envoi_devis_garage_raw = dates.date_envoi_devis_garage_raw
        orm_row.date_photos_avant = dates.date_photos_avant
        orm_row.date_photos_avant_raw = dates.date_photos_avant_raw
        orm_row.detail_complete = details.detail_complete
        orm_row.detail_error = details.detail_error

    def dossiers_needing_detail_retry(self, account_id: int) -> list[Dossier]:
        rows = self._session.execute(
            select(DossierRow).where(
                DossierRow.portal_account_id == account_id,
                DossierRow.active.is_(True),
                DossierRow.detail_complete.is_(False),
            )
        ).scalars().all()
        return [_to_domain_dossier(row) for row in rows]

    def list_for_dashboard(self, *, user_id: int) -> list[DashboardRow]:
        unread_count = (
            select(func.count())
            .select_from(NotificationRow)
            .outerjoin(
                NotificationReadRow,
                (NotificationReadRow.notification_id == NotificationRow.id)
                & (NotificationReadRow.user_id == user_id),
            )
            .where(NotificationRow.dossier_id == DossierRow.id)
            .where(NotificationReadRow.notification_id.is_(None))
            .correlate(DossierRow)
            .scalar_subquery()
        )
        detection_time = (
            select(func.max(NotificationRow.detected_at))
            .where(NotificationRow.dossier_id == DossierRow.id)
            .correlate(DossierRow)
            .scalar_subquery()
        )
        stmt = (
            select(DossierRow, DossierWorkRow, unread_count, detection_time)
            .outerjoin(DossierWorkRow, DossierWorkRow.dossier_id == DossierRow.id)
            .where(DossierRow.active.is_(True))
        )
        results = []
        for dossier_row, work_row, unread, detected in self._session.execute(stmt).all():
            results.append(
                DashboardRow(
                    dossier_id=dossier_row.id,
                    dossier_number=dossier_row.dossier_number,
                    insured_name=dossier_row.insured_name,
                    registration=dossier_row.registration,
                    garage=dossier_row.garage,
                    portal_status=dossier_row.portal_status,
                    date_envoi_devis_garage=dossier_row.date_envoi_devis_garage,
                    date_envoi_devis_garage_raw=dossier_row.date_envoi_devis_garage_raw,
                    date_fin_travaux_prevue=dossier_row.date_fin_travaux_prevue,
                    date_fin_travaux_prevue_raw=dossier_row.date_fin_travaux_prevue_raw,
                    detection_time=detected or dossier_row.first_seen_at,
                    work_status=work_row.status if work_row else WorkStatus.TO_DO,
                    unread=bool(unread),
                )
            )
        return results


class SqlAlchemyNotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, dossier_id: int, kind: NotificationKind, detected_at: datetime
    ) -> Notification:
        row = NotificationRow(dossier_id=dossier_id, kind=kind, detected_at=detected_at)
        self._session.add(row)
        self._session.flush()
        return Notification(id=row.id, dossier_id=row.dossier_id, kind=row.kind, detected_at=row.detected_at)

    def mark_all_existing_as_read_for_user(self, user_id: int, seen_at: datetime) -> None:
        already_read = select(NotificationReadRow.notification_id).where(
            NotificationReadRow.user_id == user_id
        )
        unread_ids = self._session.execute(
            select(NotificationRow.id).where(NotificationRow.id.notin_(already_read))
        ).scalars().all()
        for notification_id in unread_ids:
            self._session.add(
                NotificationReadRow(notification_id=notification_id, user_id=user_id, seen_at=seen_at)
            )

    def acknowledge_dossier(self, dossier_id: int, user_id: int, seen_at: datetime) -> None:
        already_read = select(NotificationReadRow.notification_id).where(
            NotificationReadRow.user_id == user_id
        )
        unread_ids = self._session.execute(
            select(NotificationRow.id).where(
                NotificationRow.dossier_id == dossier_id,
                NotificationRow.id.notin_(already_read),
            )
        ).scalars().all()
        for notification_id in unread_ids:
            self._session.add(
                NotificationReadRow(notification_id=notification_id, user_id=user_id, seen_at=seen_at)
            )

    def unread_count_for_user(self, user_id: int) -> int:
        already_read = select(NotificationReadRow.notification_id).where(
            NotificationReadRow.user_id == user_id
        )
        return self._session.execute(
            select(func.count()).select_from(NotificationRow).where(
                NotificationRow.id.notin_(already_read)
            )
        ).scalar_one()

    def is_unread_for_user(self, dossier_id: int, user_id: int) -> bool:
        already_read = select(NotificationReadRow.notification_id).where(
            NotificationReadRow.user_id == user_id
        )
        count = self._session.execute(
            select(func.count()).select_from(NotificationRow).where(
                NotificationRow.dossier_id == dossier_id,
                NotificationRow.id.notin_(already_read),
            )
        ).scalar_one()
        return count > 0


class SqlAlchemyPollRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, account_id: int, started_at: datetime) -> PollRun:
        row = PollRunRow(
            portal_account_id=account_id,
            started_at=started_at,
            status=PollStatus.FAILED,
            rows_seen=0,
            pages_seen=0,
            details_failed=0,
        )
        self._session.add(row)
        self._session.flush()
        return _to_domain_poll_run(row)

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
    ) -> PollRun:
        row = self._session.get(PollRunRow, poll_run_id)
        if row is None:
            raise LookupError(f"poll run {poll_run_id} not found")
        row.status = status
        row.completed_at = completed_at
        row.rows_seen = rows_seen
        row.pages_seen = pages_seen
        row.details_failed = details_failed
        row.error = error
        return _to_domain_poll_run(row)


class SqlAlchemyUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username(self, username: str) -> User | None:
        row = self._session.execute(
            select(UserRow).where(UserRow.username == username.strip().casefold())
        ).scalar_one_or_none()
        return _to_domain_user(row) if row else None

    def get(self, user_id: int) -> User | None:
        row = self._session.get(UserRow, user_id)
        return _to_domain_user(row) if row else None

    def list_all(self) -> list[User]:
        rows = self._session.execute(select(UserRow).order_by(UserRow.username)).scalars().all()
        return [_to_domain_user(row) for row in rows]

    def create(self, user: User) -> User:
        row = UserRow(
            username=user.username.strip().casefold(),
            display_name=user.display_name,
            password_hash=user.password_hash,
            role=user.role,
            active=user.active,
            created_at=user.created_at,
        )
        self._session.add(row)
        self._session.flush()
        return _to_domain_user(row)

    def set_active(self, user_id: int, active: bool) -> None:
        row = self._session.get(UserRow, user_id)
        if row is not None:
            row.active = active

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        row = self._session.get(UserRow, user_id)
        if row is not None:
            row.password_hash = password_hash


class SqlAlchemyDossierWorkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, dossier_id: int) -> DossierWork | None:
        row = self._session.get(DossierWorkRow, dossier_id)
        return _to_domain_work(row) if row else None

    def upsert(
        self,
        dossier_id: int,
        status: WorkStatus,
        expected_version: int | None,
        updated_by: int,
        updated_at: datetime,
    ) -> DossierWork:
        row = self._session.get(DossierWorkRow, dossier_id)
        if row is None:
            row = DossierWorkRow(
                dossier_id=dossier_id, status=status, version=1, updated_by=updated_by, updated_at=updated_at
            )
            self._session.add(row)
            self._session.flush()
            return _to_domain_work(row)
        if expected_version is not None and expected_version != row.version:
            raise WorkStatusConflict(dossier_id, expected_version, row.version)
        row.status = status
        row.version += 1
        row.updated_by = updated_by
        row.updated_at = updated_at
        return _to_domain_work(row)


class SqlAlchemyDossierNoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, dossier_id: int, author_id: int, body: str, created_at: datetime) -> DossierNote:
        row = DossierNoteRow(dossier_id=dossier_id, author_id=author_id, body=body, created_at=created_at)
        self._session.add(row)
        self._session.flush()
        return _to_domain_note(row)

    def list_for_dossier(self, dossier_id: int) -> list[DossierNote]:
        rows = self._session.execute(
            select(DossierNoteRow)
            .where(DossierNoteRow.dossier_id == dossier_id)
            .order_by(DossierNoteRow.created_at)
        ).scalars().all()
        return [_to_domain_note(row) for row in rows]
