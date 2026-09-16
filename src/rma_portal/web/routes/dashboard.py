from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from rma_portal.application.dashboard import compute_counts, filter_and_sort
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import WorkStatus
from rma_portal.domain.models import User
from rma_portal.web.deps import check_same_origin, get_application, require_user
from rma_portal.web.routes.auth import get_templates

router = APIRouter()

WORK_STATUS_LABELS = {
    WorkStatus.TO_DO: "À traiter",
    WorkStatus.IN_PROGRESS: "En cours",
    WorkStatus.WAITING: "En attente",
    WorkStatus.DONE: "Terminé",
}
WORK_STATUS_OPTIONS = [(status.value, label) for status, label in WORK_STATUS_LABELS.items()]


def _render_dashboard(request, template_name: str, app: Application, user: User):
    templates = get_templates(request)
    search = request.query_params.get("search") or None
    unread_only = request.query_params.get("unread_only") == "1"
    work_status_raw = request.query_params.get("work_status") or None
    portal_status = request.query_params.get("portal_status") or None
    work_status = WorkStatus(work_status_raw) if work_status_raw else None

    with app.uow_factory() as uow:
        portal_account = uow.portal_accounts.get_default()
        all_rows = uow.dossiers.list_for_dashboard(user_id=user.id)
    portal_status_options = sorted({r.portal_status for r in all_rows if r.portal_status})
    counts = compute_counts(all_rows)
    rows = filter_and_sort(
        all_rows,
        search=search,
        unread_only=unread_only,
        work_status=work_status,
        portal_status=portal_status,
    )

    return templates.TemplateResponse(
        request,
        template_name,
        {
            "user": user,
            "rows": rows,
            "counts": counts,
            "portal_account": portal_account,
            "filters": {
                "search": search,
                "unread_only": unread_only,
                "work_status": work_status_raw,
                "portal_status": portal_status,
            },
            "work_status_options": WORK_STATUS_OPTIONS,
            "work_status_labels": WORK_STATUS_LABELS,
            "portal_status_options": portal_status_options,
        },
    )


@router.get("/")
def dashboard(request: Request, app: Application = Depends(get_application), user: User = Depends(require_user)):
    is_htmx = request.headers.get("hx-request") == "true"
    template_name = "partials/dashboard_content.html" if is_htmx else "dashboard.html"
    return _render_dashboard(request, template_name, app, user)


@router.post("/refresh")
async def manual_refresh(
    request: Request,
    app: Application = Depends(get_application),
    user: User = Depends(require_user),
    _: None = Depends(check_same_origin),
):
    await app.sync_service.execute()
    return _render_dashboard(request, "partials/dashboard_content.html", app, user)
