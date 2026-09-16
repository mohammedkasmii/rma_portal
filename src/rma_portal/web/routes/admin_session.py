from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from rma_portal.bootstrap import Application
from rma_portal.domain.models import User
from rma_portal.web.deps import get_application, require_admin
from rma_portal.web.routes.auth import get_templates

router = APIRouter(prefix="/admin")


@router.get("/session")
def portal_session_status(
    request: Request, app: Application = Depends(get_application), user: User = Depends(require_admin)
):
    with app.uow_factory() as uow:
        account = uow.portal_accounts.get_default()
    return get_templates(request).TemplateResponse(
        request, "portal_session.html", {"user": user, "account": account}
    )
