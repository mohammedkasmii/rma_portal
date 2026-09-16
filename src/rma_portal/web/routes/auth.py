from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from rma_portal.bootstrap import Application
from rma_portal.domain.models import User
from rma_portal.web.deps import get_application, get_current_user, get_session_codec

router = APIRouter()


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/login")
def login_form(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
    user: User | None = Depends(get_current_user),
) -> object:
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    app: Application = Depends(get_application),
    templates: Jinja2Templates = Depends(get_templates),
) -> object:
    user = app.account_service.authenticate(username, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"user": None, "error": "Identifiants invalides ou compte désactivé."},
            status_code=401,
        )
    codec = get_session_codec(request)
    token = codec.encode(user.id)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        app.settings.session_cookie_name,
        token,
        max_age=app.settings.session_lifetime_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


@router.post("/logout")
def logout(request: Request, app: Application = Depends(get_application)) -> object:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(app.settings.session_cookie_name)
    return response
