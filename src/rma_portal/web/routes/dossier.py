from __future__ import annotations

from urllib.parse import urljoin

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from rma_portal.application.dossier_service import DossierNotFoundError
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import WorkStatus
from rma_portal.domain.models import EmptyNoteError, NoteTooLongError, User, WorkStatusConflict
from rma_portal.web.deps import check_same_origin, get_application, require_user
from rma_portal.web.routes.auth import get_templates
from rma_portal.web.routes.dashboard import WORK_STATUS_OPTIONS

router = APIRouter()


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

    return get_templates(request).TemplateResponse(
        request,
        "dossier.html",
        {
            "user": user,
            "dossier": dossier,
            "work": work,
            "notes": notes,
            "work_status_options": WORK_STATUS_OPTIONS,
            "omegaflow_details_url": urljoin(app.settings.omegaflow_base_url, dossier.details_href),
        },
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
    version = int(expected_version) if expected_version.strip() else None
    try:
        app.dossier_service.update_work_status(
            dossier_id=dossier_id,
            status=WorkStatus(status),
            expected_version=version,
            user_id=user.id,
        )
    except WorkStatusConflict:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "user": user,
                "title": "Conflit de mise à jour",
                "message": "Ce dossier a été modifié entre-temps. Rechargez la page et réessayez.",
            },
            status_code=409,
        )
    return RedirectResponse(f"/dossiers/{dossier_id}", status_code=303)


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
    try:
        app.dossier_service.add_note(dossier_id=dossier_id, author_id=user.id, body=body)
    except (NoteTooLongError, EmptyNoteError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "user": user,
                "title": "Note invalide",
                "message": "La note doit contenir entre 1 et 2000 caractères.",
            },
            status_code=400,
        )
    return RedirectResponse(f"/dossiers/{dossier_id}", status_code=303)
