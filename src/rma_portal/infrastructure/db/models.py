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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rma_portal.domain.enums import (
    MAX_NOTE_LENGTH,
    NotificationKind,
    PollStatus,
    Role,
    SessionStatus,
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
