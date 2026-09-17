from __future__ import annotations

from urllib.parse import urljoin

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from rma_portal.application.dossier_service import DossierNotFoundError
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import WorkStatus
from rma_portal.domain.models import EmptyNoteError, NoteTooLongError, User, WorkStatusConflict
from rma_portal.web.deps import check_same_origin, get_application, require_user
from rma_portal.web.routes.auth import get_templates
from rma_portal.web.routes.dashboard import WORK_STATUS_OPTIONS

router = APIRouter()

_STATUS_CONFLICT_MESSAGE = "Ce dossier a été modifié entre-temps. Rechargez la page et réessayez."
_NOTE_INVALID_MESSAGE = "La note doit contenir entre 1 et 2000 caractères."


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _users_by_id(app: Application) -> dict[int | None, User]:
    """All users keyed by id, including disabled ones, for note attribution."""
    return {u.id: u for u in app.account_service.list_users()}


@router.get("/dossiers/{dossier_id}")
def dossier_detail(
    dossier_id: int,
    request: Request,
    app: Application = Depends(get_application),
    user: User = Depends(require_user),
):
    try:
        dossier, work, notes = app.dossier_service.get_dossier(dossier_id)
    except DossierNotFoundError:
        return get_templates(request).TemplateResponse(
            request,
            "error.html",
            {"user": user, "title": "Dossier introuvable", "message": "Ce dossier n'existe pas."},
            status_code=404,
        )
    # Opening the dossier page is the explicit per-employee acknowledgement.
    app.dossier_service.acknowledge(dossier_id, user.id)

    template_name = "partials/dossier_content.html" if _is_htmx(request) else "dossier.html"
    return get_templates(request).TemplateResponse(
        request,
        template_name,
        {
            "user": user,
            "dossier": dossier,
            "dossier_id": dossier_id,
            "work": work,
            "notes": notes,
            "users_by_id": _users_by_id(app),
            "work_status_options": WORK_STATUS_OPTIONS,
            "omegaflow_details_url": urljoin(app.settings.omegaflow_base_url, dossier.details_href),
        },
    )


def _render_work_section(
    templates: Jinja2Templates,
    request: Request,
    *,
    user: User,
    dossier_id: int,
    work,
    message: str,
    message_class: str,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "partials/dossier_work_section.html",
        {
            "user": user,
            "dossier_id": dossier_id,
            "work": work,
            "work_status_options": WORK_STATUS_OPTIONS,
            "status_message": message,
            "status_message_class": message_class,
        },
        status_code=status_code,
    )


@router.post("/dossiers/{dossier_id}/status")
def update_status(
    dossier_id: int,
    request: Request,
    status: str = Form(...),
    expected_version: str = Form(""),
    app: Application = Depends(get_application),
    user: User = Depends(require_user),
    _: None = Depends(check_same_origin),
):
    templates = get_templates(request)
    htmx = _is_htmx(request)
    version = int(expected_version) if expected_version.strip() else None
    try:
        work = app.dossier_service.update_work_status(
            dossier_id=dossier_id,
            status=WorkStatus(status),
            expected_version=version,
            # Only the acting employee's own view is affected -- this never
            # touches other employees' unread notifications (see
            # notifications.acknowledge_dossier, which is never called here).
            user_id=user.id,
        )
    except WorkStatusConflict:
        if not htmx:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"user": user, "title": "Conflit de mise à jour", "message": _STATUS_CONFLICT_MESSAGE},
                status_code=409,
            )
        # Re-render with the real, current state so the visible selection
        # and the hidden expected_version both match the database again.
        try:
            _dossier, current_work, _notes = app.dossier_service.get_dossier(dossier_id)
        except DossierNotFoundError:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"user": user, "title": "Dossier introuvable", "message": "Ce dossier n'existe pas."},
                status_code=404,
            )
        return _render_work_section(
            templates,
            request,
            user=user,
            dossier_id=dossier_id,
            work=current_work,
            message=_STATUS_CONFLICT_MESSAGE,
            message_class="error",
        )

    if not htmx:
        return RedirectResponse(f"/dossiers/{dossier_id}", status_code=303)
    return _render_work_section(
        templates,
        request,
        user=user,
        dossier_id=dossier_id,
        work=work,
        message="Statut mis à jour.",
        message_class="success",
    )


def _render_notes_section(
    templates: Jinja2Templates,
    request: Request,
    app: Application,
    *,
    user: User,
    dossier_id: int,
    message: str,
    message_class: str,
):
    _dossier, _work, notes = app.dossier_service.get_dossier(dossier_id)
    return templates.TemplateResponse(
        request,
        "partials/dossier_notes_section.html",
        {
            "user": user,
            "dossier_id": dossier_id,
            "notes": notes,
            "users_by_id": _users_by_id(app),
            "note_message": message,
            "note_message_class": message_class,
        },
    )


@router.post("/dossiers/{dossier_id}/notes")
def add_note(
    dossier_id: int,
    request: Request,
    body: str = Form(...),
    app: Application = Depends(get_application),
    user: User = Depends(require_user),
    _: None = Depends(check_same_origin),
):
    templates = get_templates(request)
    htmx = _is_htmx(request)
    try:
        app.dossier_service.add_note(dossier_id=dossier_id, author_id=user.id, body=body)
    except (NoteTooLongError, EmptyNoteError):
        if not htmx:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"user": user, "title": "Note invalide", "message": _NOTE_INVALID_MESSAGE},
                status_code=400,
            )
        return _render_notes_section(
            templates,
            request,
            app,
            user=user,
            dossier_id=dossier_id,
            message=_NOTE_INVALID_MESSAGE,
            message_class="error",
        )

    if not htmx:
        return RedirectResponse(f"/dossiers/{dossier_id}", status_code=303)
    return _render_notes_section(
        templates,
        request,
        app,
        user=user,
        dossier_id=dossier_id,
        message="Note ajoutée.",
        message_class="success",
    )
