from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from rma_portal.bootstrap import Application
from rma_portal.domain.models import User
from rma_portal.web.deps import check_same_origin, get_application, require_user
from rma_portal.web.routes.auth import get_templates
from rma_portal.web.session_view import build_session_view

router = APIRouter()


@router.post("/session/connect")
async def connect_session(
    request: Request,
    app: Application = Depends(get_application),
    user: User = Depends(require_user),
    _: None = Depends(check_same_origin),
):
    # Must be `async def`: SessionConnector.start() calls asyncio.create_task,
    # which requires the event loop of the *current* thread -- a `def` route
    # would run in FastAPI's worker threadpool instead and have none.
    # start() is a no-op (returns False) if a window is already open, so a
    # duplicate click can never open a second browser; either way the
    # response shows the current (now-CONNECTING) state.
    app.session_connector.start()
    with app.uow_factory() as uow:
        portal_account = uow.portal_accounts.get_default()
    session = build_session_view(app, portal_account)
    return get_templates(request).TemplateResponse(
        request, "partials/session_card.html", {"user": user, "session": session}
    )
