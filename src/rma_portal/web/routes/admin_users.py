from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from rma_portal.bootstrap import Application
from rma_portal.domain.enums import Role
from rma_portal.domain.models import CannotDisableSelfError, DuplicateUsernameError, User
from rma_portal.infrastructure.security.passwords import WeakPasswordError
from rma_portal.web.deps import check_same_origin, get_application, require_admin
from rma_portal.web.routes.auth import get_templates

router = APIRouter(prefix="/admin")


def _render(request: Request, app: Application, user: User, **extra):
    return get_templates(request).TemplateResponse(
        request,
        "admin_users.html",
        {"user": user, "users": app.account_service.list_users(), **extra},
    )


@router.get("/users")
def list_users(
    request: Request, app: Application = Depends(get_application), user: User = Depends(require_admin)
):
    return _render(request, app, user)


@router.post("/users")
def create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    role: str = Form("EMPLOYEE"),
    app: Application = Depends(get_application),
    user: User = Depends(require_admin),
    _: None = Depends(check_same_origin),
):
    try:
        app.account_service.create_user(
            username=username, display_name=display_name, password=password, role=Role(role)
        )
    except DuplicateUsernameError:
        return _render(request, app, user, error="Cet identifiant existe déjà.")
    except WeakPasswordError as exc:
        return _render(request, app, user, error=str(exc))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-active")
def toggle_active(
    user_id: int,
    request: Request,
    active: str = Form(...),
    app: Application = Depends(get_application),
    user: User = Depends(require_admin),
    _: None = Depends(check_same_origin),
):
    try:
        app.account_service.set_active(user_id=user_id, active=active == "1", acting_admin_id=user.id)
    except CannotDisableSelfError:
        return _render(
            request, app, user, error="Vous ne pouvez pas désactiver votre propre compte actif."
        )
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    app: Application = Depends(get_application),
    user: User = Depends(require_admin),
    _: None = Depends(check_same_origin),
):
    try:
        app.account_service.reset_password(user_id=user_id, new_password=new_password)
    except WeakPasswordError as exc:
        return _render(request, app, user, error=str(exc))
    return RedirectResponse("/admin/users", status_code=303)
