"""Garage agréé dossier use cases for the pre-V2 (Jinja) pages.

Work status and read state now belong to a *workflow membership* and an
*occurrence*. These legacy pages only know the Garage agréé queue, so every
operation here resolves that workflow's membership for the dossier; the V2 JSON
API (``application.work_service``) exposes the same data for every workflow.
Notes stay shared at dossier level.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rma_portal.application.ports import UnitOfWork, UnitOfWorkFactory
from rma_portal.domain.enums import MAX_NOTE_LENGTH, WorkStatus
from rma_portal.domain.models import (
    Dossier,
    DossierNote,
    DossierWork,
    EmptyNoteError,
    NoteTooLongError,
    WorkflowMembership,
)

GARAGE_WORKFLOW_KEY = "agreement_garage"


class DossierNotFoundError(Exception):
    def __init__(self, dossier_id: int) -> None:
        super().__init__(f"dossier {dossier_id} not found")
        self.dossier_id = dossier_id


def _garage_membership(uow: UnitOfWork, dossier_id: int) -> WorkflowMembership | None:
    account = uow.portal_accounts.get_default()
    if account is None or account.id is None:
        return None
    workflow = uow.workflows.get_by_key(account.id, GARAGE_WORKFLOW_KEY)
    if workflow is None or workflow.id is None:
        return None
    return uow.workflow_memberships.get(workflow.id, dossier_id)


class DossierService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_dossier(self, dossier_id: int) -> tuple[Dossier, DossierWork | None, list[DossierNote]]:
        with self._uow_factory() as uow:
            dossier = uow.dossiers.get(dossier_id)
            if dossier is None:
                raise DossierNotFoundError(dossier_id)
            membership = _garage_membership(uow, dossier_id)
            work = None
            if membership is not None and membership.id is not None:
                stored = uow.workflow_work.get(membership.id)
                if stored is not None:
                    work = DossierWork(
                        dossier_id=dossier_id,
                        status=stored.status,
                        version=stored.version,
                        updated_by=stored.updated_by,
                        updated_at=stored.updated_at,
                    )
            notes = uow.dossier_notes.list_for_dossier(dossier_id)
            return dossier, work, notes

    def acknowledge(self, dossier_id: int, user_id: int) -> None:
        """Opening the page reads the Garage agréé occurrences for this employee only."""
        with self._uow_factory() as uow:
            membership = _garage_membership(uow, dossier_id)
            if membership is None or membership.id is None:
                return
            now = datetime.now(UTC)
            for occurrence in uow.workflow_occurrences.list_for_membership(membership.id):
                if occurrence.id is not None:
                    uow.notifications.acknowledge_occurrence(occurrence.id, user_id, now)
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
            membership = _garage_membership(uow, dossier_id)
            if membership is None or membership.id is None:
                raise DossierNotFoundError(dossier_id)
            stored = uow.workflow_work.upsert(
                membership.id, status, expected_version, user_id, datetime.now(UTC)
            )
            uow.commit()
            return DossierWork(
                dossier_id=dossier_id,
                status=stored.status,
                version=stored.version,
                updated_by=stored.updated_by,
                updated_at=stored.updated_at,
            )

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
