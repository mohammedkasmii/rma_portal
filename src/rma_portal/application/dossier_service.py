"""Dossier-facing use cases: dashboard, acknowledgement, work status, notes."""

from __future__ import annotations

from datetime import UTC, datetime

from rma_portal.application.ports import UnitOfWorkFactory
from rma_portal.domain.enums import MAX_NOTE_LENGTH, WorkStatus
from rma_portal.domain.models import (
    Dossier,
    DossierNote,
    DossierWork,
    EmptyNoteError,
    NoteTooLongError,
)


class DossierNotFoundError(Exception):
    def __init__(self, dossier_id: int) -> None:
        super().__init__(f"dossier {dossier_id} not found")
        self.dossier_id = dossier_id


class DossierService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_dossier(self, dossier_id: int) -> tuple[Dossier, DossierWork | None, list[DossierNote]]:
        with self._uow_factory() as uow:
            dossier = uow.dossiers.get(dossier_id)
            if dossier is None:
                raise DossierNotFoundError(dossier_id)
            work = uow.dossier_work.get(dossier_id)
            notes = uow.dossier_notes.list_for_dossier(dossier_id)
            return dossier, work, notes

    def acknowledge(self, dossier_id: int, user_id: int) -> None:
        with self._uow_factory() as uow:
            uow.notifications.acknowledge_dossier(dossier_id, user_id, datetime.now(UTC))
            uow.commit()

    def update_work_status(
        self,
        *,
        dossier_id: int,
        status: WorkStatus,
        expected_version: int | None,
        user_id: int,
    ) -> DossierWork:
        with self._uow_factory() as uow:
            work = uow.dossier_work.upsert(
                dossier_id, status, expected_version, user_id, datetime.now(UTC)
            )
            uow.commit()
            return work

    def add_note(self, *, dossier_id: int, author_id: int, body: str) -> DossierNote:
        body = body.strip()
        if len(body) > MAX_NOTE_LENGTH:
            raise NoteTooLongError(len(body), MAX_NOTE_LENGTH)
        if not body:
            raise EmptyNoteError()
        with self._uow_factory() as uow:
            note = uow.dossier_notes.add(dossier_id, author_id, body, datetime.now(UTC))
            uow.commit()
            return note
