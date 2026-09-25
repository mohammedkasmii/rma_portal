"""SQLAlchemy 2.0 ORM models.

This is the only module allowed to import SQLAlchemy outside of
``infrastructure.db``. Column shapes mirror ``domain.models`` exactly; the
repositories in ``repositories.py`` translate between the two.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rma_portal.domain.enums import (
    MAX_NOTE_LENGTH,
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
from rma_portal.infrastructure.db.base import Base


def _enum_column(enum_cls: type[enum.Enum], length: int) -> Enum:
    """A VARCHAR-backed enum column that round-trips to real Python enums.

    Plain ``String`` columns hand back a bare ``str`` on read, which quietly
    breaks any ``is``/``is not`` enum comparison (e.g. ``role is Role.ADMIN``)
    even though ``==`` still works by accident for ``StrEnum``. Using
    SQLAlchemy's ``Enum`` type (with ``native_enum=False`` so SQLite still
    stores plain text) makes both comparisons correct.
    """
    return Enum(enum_cls, native_enum=False, length=length, validate_strings=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[Role] = mapped_column(_enum_column(Role, 20), nullable=False, default=Role.EMPLOYEE)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PortalAccountRow(Base):
    __tablename__ = "portal_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_status: Mapped[SessionStatus] = mapped_column(
        _enum_column(SessionStatus, 20), nullable=False, default=SessionStatus.UNKNOWN
    )
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))


class WorkflowRow(Base):
    """Technical definition and independent poll state for one queue."""

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("portal_account_id", "key", name="uq_workflow_account_key"),
        Index("ix_workflow_account_enabled", "portal_account_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_account_id: Mapped[int] = mapped_column(
        ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(150), nullable=False)
    route: Mapped[str] = mapped_column(String(500), nullable=False)
    view_id: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules_status: Mapped[WorkflowRulesStatus] = mapped_column(
        _enum_column(WorkflowRulesStatus, 20),
        nullable=False,
        default=WorkflowRulesStatus.UNCONFIRMED,
    )
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    notification_class: Mapped[NotificationClass] = mapped_column(
        _enum_column(NotificationClass, 20), nullable=False, default=NotificationClass.ACTION
    )
    catalog_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    filter_field: Mapped[str | None] = mapped_column(String(50))
    filter_operator: Mapped[str | None] = mapped_column(String(20))
    filter_value: Mapped[str | None] = mapped_column(String(100))
    filter_label: Mapped[str | None] = mapped_column(String(200))
    primary_date_key: Mapped[str | None] = mapped_column(String(100))
    last_poll_status: Mapped[PollStatus | None] = mapped_column(_enum_column(PollStatus, 20))

    memberships: Mapped[list[WorkflowMembershipRow]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class DossierRow(Base):
    __tablename__ = "dossiers"
    __table_args__ = (
        UniqueConstraint("portal_account_id", "record_id", name="uq_dossier_account_record"),
        Index("ix_dossier_active", "portal_account_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_account_id: Mapped[int] = mapped_column(
        ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)

    dossier_number: Mapped[str] = mapped_column(String(200), default="")
    insured_name: Mapped[str] = mapped_column(String(300), default="")
    procedure: Mapped[str] = mapped_column(String(200), default="")
    registration: Mapped[str] = mapped_column(String(100), default="")
    garage: Mapped[str] = mapped_column(String(300), default="")
    estimate_amount_raw: Mapped[str] = mapped_column(String(100), default="")
    portal_status: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(200), default="")
    observation_count: Mapped[str] = mapped_column(String(50), default="")
    agreement_login: Mapped[str] = mapped_column(String(200), default="")
    details_href: Mapped[str] = mapped_column(String(1000), default="")

    date_creation: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_creation_raw: Mapped[str] = mapped_column(String(100), default="")
    date_premiere_fin_prevue: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_premiere_fin_prevue_raw: Mapped[str] = mapped_column(String(100), default="")
    date_fin_travaux_prevue: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_fin_travaux_prevue_raw: Mapped[str] = mapped_column(String(100), default="")
    date_envoi_devis_garage: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_envoi_devis_garage_raw: Mapped[str] = mapped_column(String(100), default="")
    date_photos_avant: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_photos_avant_raw: Mapped[str] = mapped_column(String(100), default="")

    detail_complete: Mapped[bool] = mapped_column(default=False, nullable=False)
    detail_error: Mapped[str | None] = mapped_column(String(1000))
    detail_fields_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    missing_complete_polls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    notifications: Mapped[list[NotificationRow]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan"
    )
    work: Mapped[DossierWorkRow | None] = relationship(
        back_populates="dossier", cascade="all, delete-orphan", uselist=False
    )
    notes: Mapped[list[DossierNoteRow]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan", order_by="DossierNoteRow.created_at"
    )
    workflow_memberships: Mapped[list[WorkflowMembershipRow]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan"
    )


class WorkflowMembershipRow(Base):
    """Queue-specific lifecycle for a shared dossier."""

    __tablename__ = "workflow_memberships"
    __table_args__ = (
        UniqueConstraint("workflow_id", "dossier_id", name="uq_membership_workflow_dossier"),
        Index("ix_membership_workflow_active", "workflow_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    missing_complete_polls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    occurrence_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    captured_fields_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow: Mapped[WorkflowRow] = relationship(back_populates="memberships")
    dossier: Mapped[DossierRow] = relationship(back_populates="workflow_memberships")
    occurrences: Mapped[list[WorkflowOccurrenceRow]] = relationship(
        back_populates="membership",
        cascade="all, delete-orphan",
        order_by="WorkflowOccurrenceRow.occurrence_number",
    )
    work: Mapped[WorkflowWorkRow | None] = relationship(
        back_populates="membership", cascade="all, delete-orphan", uselist=False
    )


class WorkflowOccurrenceRow(Base):
    """Insert-only record of one appearance of a dossier in a workflow.

    Database triggers (see the V2 migration) reject UPDATEs, so notification
    history can never be rewritten by a later reappearance.
    """

    __tablename__ = "workflow_occurrences"
    __table_args__ = (
        UniqueConstraint("membership_id", "occurrence_number", name="uq_occurrence_membership_number"),
        Index("ix_occurrence_workflow_dossier", "workflow_id", "dossier_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_memberships.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurrence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[OccurrenceOrigin] = mapped_column(_enum_column(OccurrenceOrigin, 20), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    membership: Mapped[WorkflowMembershipRow] = relationship(back_populates="occurrences")


class WorkflowWorkRow(Base):
    """Shared work status, owned by a workflow membership (not the dossier)."""

    __tablename__ = "workflow_work"

    membership_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[WorkStatus] = mapped_column(
        _enum_column(WorkStatus, 20), nullable=False, default=WorkStatus.TO_DO
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    membership: Mapped[WorkflowMembershipRow] = relationship(back_populates="work")


class NotificationRow(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        _enum_column(NotificationKind, 50), nullable=False, default=NotificationKind.NEW_AGREEMENT_DOSSIER
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    workflow_occurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_occurrences.id", ondelete="CASCADE"), index=True
    )
    workflow_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_events.id", ondelete="SET NULL")
    )

    dossier: Mapped[DossierRow] = relationship(back_populates="notifications")
    reads: Mapped[list[NotificationReadRow]] = relationship(
        back_populates="notification", cascade="all, delete-orphan"
    )


class NotificationReadRow(Base):
    __tablename__ = "notification_reads"

    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    notification: Mapped[NotificationRow] = relationship(back_populates="reads")


class DossierWorkRow(Base):
    __tablename__ = "dossier_work"

    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[WorkStatus] = mapped_column(_enum_column(WorkStatus, 20), nullable=False, default=WorkStatus.TO_DO)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    dossier: Mapped[DossierRow] = relationship(back_populates="work")


class DossierNoteRow(Base):
    __tablename__ = "dossier_notes"
    __table_args__ = (
        CheckConstraint(f"length(body) <= {MAX_NOTE_LENGTH}", name="ck_note_max_length"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    body: Mapped[str] = mapped_column(String(MAX_NOTE_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workflow_membership_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_memberships.id", ondelete="SET NULL")
    )

    dossier: Mapped[DossierRow] = relationship(back_populates="notes")


class PollRunRow(Base):
    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_account_id: Mapped[int] = mapped_column(
        ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PollStatus] = mapped_column(_enum_column(PollStatus, 20), nullable=False)
    rows_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000))


class SyncRunRow(Base):
    """Parent record of one complete synchronization cycle."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_account_id: Mapped[int] = mapped_column(
        ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[SyncTrigger] = mapped_column(
        _enum_column(SyncTrigger, 20), nullable=False, default=SyncTrigger.SCHEDULED
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PollStatus] = mapped_column(_enum_column(PollStatus, 20), nullable=False)
    workflows_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workflows_complete: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workflows_partial: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workflows_auth_required: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workflows_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000))

    workflow_runs: Mapped[list[WorkflowPollRunRow]] = relationship(
        back_populates="sync_run", cascade="all, delete-orphan"
    )


class WorkflowPollRunRow(Base):
    """Independent outcome of one workflow inside a sync run."""

    __tablename__ = "workflow_poll_runs"
    __table_args__ = (Index("ix_workflow_poll_run_workflow", "workflow_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PollStatus] = mapped_column(_enum_column(PollStatus, 20), nullable=False)
    baseline: Mapped[bool] = mapped_column(default=False, nullable=False)
    rows_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    returned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    left_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notifications_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000))

    sync_run: Mapped[SyncRunRow] = relationship(back_populates="workflow_runs")


class WorkflowEventRow(Base):
    """Insert-only activity feed. Holds field names and fingerprints only."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        Index("ix_workflow_event_workflow_time", "workflow_id", "detected_at"),
        Index("ix_workflow_event_dossier_time", "dossier_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    membership_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_memberships.id", ondelete="CASCADE")
    )
    occurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_occurrences.id", ondelete="CASCADE")
    )
    dossier_id: Mapped[int | None] = mapped_column(ForeignKey("dossiers.id", ondelete="CASCADE"))
    poll_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_poll_runs.id", ondelete="CASCADE")
    )
    kind: Mapped[WorkflowEventKind] = mapped_column(_enum_column(WorkflowEventKind, 40), nullable=False)
    notification_class: Mapped[NotificationClass] = mapped_column(
        _enum_column(NotificationClass, 20), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_fields_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    before_fingerprints_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    after_fingerprints_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    message: Mapped[str | None] = mapped_column(String(500))


class OutboxMessageRow(Base):
    """Transactional outbox: written with reconciliation, processed afterwards."""

    __tablename__ = "outbox_messages"
    __table_args__ = (Index("ix_outbox_pending", "processed_at", "available_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000))


class AiRunRow(Base):
    """Audit of one optional AI request. Never holds credentials or browser state."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_run_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feature: Mapped[AiFeature] = mapped_column(_enum_column(AiFeature, 40), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(30), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AiRunStatus] = mapped_column(_enum_column(AiRunStatus, 20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
