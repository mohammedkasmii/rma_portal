"""Employee workspace endpoints: dashboard, workflows, inbox, dossiers, work status, notes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rma_portal.application.dossier_service import DossierNotFoundError
from rma_portal.application.views import (
    DashboardView,
    DossierHitView,
    DossierView,
    ItemPage,
    NoteView,
    WorkflowDetailView,
    WorkflowView,
    WorkStateView,
)
from rma_portal.application.work_service import (
    ItemQuery,
    MembershipNotFoundError,
    OccurrenceNotFoundError,
    WorkflowNotFoundError,
    WorkService,
)
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import MAX_NOTE_LENGTH, WorkStatus
from rma_portal.domain.models import EmptyNoteError, NoteTooLongError, User, WorkStatusConflict
from rma_portal.web.api.deps import api_user, get_work_service, live_session_state, same_origin
from rma_portal.web.deps import get_application

router = APIRouter(tags=["workspace"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=message)


@router.get("/dashboard", response_model=DashboardView)
def dashboard(
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
    app: Application = Depends(get_application),
) -> DashboardView:
    return service.dashboard(user.id, live_session_state(app))


@router.get("/workflows", response_model=list[WorkflowView])
def workflows(
    user: User = Depends(api_user), service: WorkService = Depends(get_work_service)
) -> list[WorkflowView]:
    return service.workflows(user.id)


@router.get("/workflows/{key}", response_model=WorkflowDetailView)
def workflow_detail(
    key: str, user: User = Depends(api_user), service: WorkService = Depends(get_work_service)
) -> WorkflowDetailView:
    try:
        return service.workflow_detail(user.id, key)
    except WorkflowNotFoundError:
        raise _not_found("Ce workflow n'existe pas.") from None


def _query(
    *,
    workflow: list[str] | None,
    category: str | None,
    search: str | None,
    unread: bool,
    kind: str | None,
    work_status: WorkStatus | None,
    portal_status: str | None,
    procedure: str | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> ItemQuery:
    return ItemQuery(
        workflow_keys=tuple(workflow or ()),
        category=category,
        search=search,
        unread_only=unread,
        unread_kind=kind,
        work_status=work_status,
        portal_status=portal_status,
        procedure=procedure,
        sort=sort,
        descending=order != "asc",
        page=page,
        page_size=page_size,
    )


@router.get("/inbox", response_model=ItemPage)
def inbox(
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
    workflow: Annotated[list[str] | None, Query()] = None,
    category: str | None = None,
    search: str | None = Query(None, max_length=100),
    unread: bool = False,
    kind: str | None = Query(None, pattern="^(?i:arrival|changed)$"),
    work_status: WorkStatus | None = None,
    portal_status: str | None = None,
    procedure: str | None = None,
    sort: str = Query("default", pattern="^(default|date|detected|age|number|name|status|workflow)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> ItemPage:
    query = _query(
        workflow=workflow, category=category, search=search, unread=unread, kind=kind,
        work_status=work_status, portal_status=portal_status, procedure=procedure,
        sort=sort, order=order, page=page, page_size=page_size,
    )
    try:
        return service.items(user.id, query)
    except WorkflowNotFoundError:
        raise _not_found("Ce workflow n'existe pas.") from None


@router.get("/workflows/{key}/items", response_model=ItemPage)
def workflow_items(
    key: str,
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
    search: str | None = Query(None, max_length=100),
    unread: bool = False,
    kind: str | None = Query(None, pattern="^(?i:arrival|changed)$"),
    work_status: WorkStatus | None = None,
    portal_status: str | None = None,
    procedure: str | None = None,
    sort: str = Query("default", pattern="^(default|date|detected|age|number|name|status|workflow)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> ItemPage:
    query = _query(
        workflow=[key], category=None, search=search, unread=unread, kind=kind,
        work_status=work_status, portal_status=portal_status, procedure=procedure,
        sort=sort, order=order, page=page, page_size=page_size,
    )
    try:
        return service.items(user.id, query)
    except WorkflowNotFoundError:
        raise _not_found("Ce workflow n'existe pas.") from None


@router.get("/dossiers/search", response_model=list[DossierHitView])
def search_dossiers(
    q: str = Query(..., min_length=2, max_length=100),
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
) -> list[DossierHitView]:
    del user
    return service.search(q)


@router.get("/dossiers/{dossier_id}", response_model=DossierView)
def dossier(
    dossier_id: int, user: User = Depends(api_user), service: WorkService = Depends(get_work_service)
) -> DossierView:
    """Read-only: opening this endpoint never acknowledges anything."""
    try:
        return service.dossier(user.id, dossier_id)
    except DossierNotFoundError:
        raise _not_found("Ce dossier n'existe pas.") from None


class AcknowledgeOut(BaseModel):
    occurrence_id: int
    acknowledged: int


@router.post(
    "/occurrences/{occurrence_id}/acknowledge",
    response_model=AcknowledgeOut,
    dependencies=[same_origin],
)
def acknowledge(
    occurrence_id: int,
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
) -> AcknowledgeOut:
    """Opening an occurrence marks its alerts read for the acting employee only."""
    try:
        count = service.acknowledge_occurrence(user.id, occurrence_id)
    except OccurrenceNotFoundError:
        raise _not_found("Cette occurrence n'existe pas.") from None
    return AcknowledgeOut(occurrence_id=occurrence_id, acknowledged=count)


class WorkStatusIn(BaseModel):
    status: WorkStatus
    expected_version: int | None = Field(default=None, ge=1)


@router.put(
    "/memberships/{membership_id}/work-status",
    response_model=WorkStateView,
    dependencies=[same_origin],
    responses={409: {"description": "Le statut a été modifié entre-temps."}},
)
def set_work_status(
    membership_id: int,
    body: WorkStatusIn,
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
):
    """Shared work status of one dossier *in one workflow*."""
    try:
        return service.set_work_status(user.id, membership_id, body.status, body.expected_version)
    except MembershipNotFoundError:
        raise _not_found("Ce dossier n'est pas dans ce workflow.") from None
    except WorkStatusConflict:
        current = service.current_work_state(membership_id)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "Ce statut a été modifié entre-temps. Rechargez et réessayez.",
                "current": {
                    "membership_id": current.membership_id,
                    "status": current.status.value,
                    "version": current.version,
                    "updated_at": current.updated_at.isoformat(),
                    "updated_by": current.updated_by,
                },
            },
        )


class NoteIn(BaseModel):
    body: str = Field(max_length=MAX_NOTE_LENGTH * 2)
    membership_id: int | None = None


@router.get("/dossiers/{dossier_id}/notes", response_model=list[NoteView])
def notes(
    dossier_id: int, user: User = Depends(api_user), service: WorkService = Depends(get_work_service)
) -> list[NoteView]:
    del user
    try:
        return service.notes(dossier_id)
    except DossierNotFoundError:
        raise _not_found("Ce dossier n'existe pas.") from None


@router.post(
    "/dossiers/{dossier_id}/notes",
    response_model=NoteView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[same_origin],
)
def add_note(
    dossier_id: int,
    body: NoteIn,
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
) -> NoteView:
    """Notes are shared at dossier level; ``membership_id`` only adds workflow context."""
    try:
        return service.add_note(user.id, dossier_id, body.body, body.membership_id)
    except DossierNotFoundError:
        raise _not_found("Ce dossier n'existe pas.") from None
    except MembershipNotFoundError:
        raise _not_found("Ce dossier n'est pas dans ce workflow.") from None
    except (EmptyNoteError, NoteTooLongError):
        raise HTTPException(
            422,
            detail=f"La note doit contenir entre 1 et {MAX_NOTE_LENGTH} caractères.",
        ) from None
