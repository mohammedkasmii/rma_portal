"""Session health, synchronization health and administration endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from rma_portal.application.accounts import AccountService
from rma_portal.application.views import SessionHealthView, SyncHealthView, WorkflowView
from rma_portal.application.work_service import WorkflowNotFoundError, WorkService
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import NotificationClass, Role, WorkflowRulesStatus
from rma_portal.domain.models import (
    CannotDisableSelfError,
    DuplicateUsernameError,
    User,
)
from rma_portal.infrastructure.portal.browser_control import BrowserControlError
from rma_portal.infrastructure.security.passwords import WeakPasswordError
from rma_portal.web.api.auth import UserOut, user_out
from rma_portal.web.api.deps import (
    api_admin,
    api_user,
    get_work_service,
    live_session_state,
    same_origin,
)
from rma_portal.web.deps import get_application

router = APIRouter(tags=["operations"])


@router.get("/session", response_model=SessionHealthView)
def session(
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
    app: Application = Depends(get_application),
) -> SessionHealthView:
    """Health of the OmegaFlow session. Never exposes cookies, storage state or credentials."""
    del user
    return service.session_health(live_session_state(app))


@router.get("/sync", response_model=SyncHealthView)
def sync_health(
    user: User = Depends(api_user),
    service: WorkService = Depends(get_work_service),
    app: Application = Depends(get_application),
) -> SyncHealthView:
    return service.sync_health(user.id, live_session_state(app))


class SyncRequestOut(BaseModel):
    queued: bool


@router.post(
    "/sync/run",
    response_model=SyncRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[same_origin],
)
def request_sync(
    user: User = Depends(api_user), service: WorkService = Depends(get_work_service)
) -> SyncRequestOut:
    """Ask the worker for an immediate cycle; the API process never opens the browser."""
    del user
    return SyncRequestOut(queued=service.request_sync())


class SessionConnectOut(BaseModel):
    started: bool
    state: str
    connect_url: str


@router.post(
    "/admin/session/connect",
    response_model=SessionConnectOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[same_origin],
)
async def connect_session(
    user: User = Depends(api_admin), app: Application = Depends(get_application)
) -> SessionConnectOut:
    """Start the isolated visible browser; credentials stay inside noVNC."""
    del user
    if app.browser_control is None or not app.settings.novnc_url:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le navigateur de connexion n'est pas configuré sur ce serveur.",
        )
    try:
        result = await asyncio.to_thread(app.browser_control.start_login)
    except BrowserControlError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le navigateur de connexion est momentanément indisponible.",
        ) from None
    return SessionConnectOut(
        started=result.started,
        state="started" if result.started else "already_running",
        connect_url=app.settings.novnc_url,
    )


# --- administration -----------------------------------------------------------------------------

admin = APIRouter(prefix="/admin", tags=["admin"])


@admin.get("/workflows", response_model=list[WorkflowView])
def admin_workflows(
    user: User = Depends(api_admin), service: WorkService = Depends(get_work_service)
) -> list[WorkflowView]:
    return service.workflows(user.id)


class WorkflowConfigIn(BaseModel):
    enabled: bool | None = None
    rules_status: WorkflowRulesStatus | None = None
    notification_class: NotificationClass | None = None


@admin.patch("/workflows/{key}", response_model=WorkflowView, dependencies=[same_origin])
def update_workflow(
    key: str,
    body: WorkflowConfigIn,
    user: User = Depends(api_admin),
    service: WorkService = Depends(get_work_service),
) -> WorkflowView:
    """Enable/disable a queue, promote its rules (À valider sur site -> confirmé) or change
    how its activity reaches employees. The event engine itself never changes."""
    try:
        return service.update_workflow(
            user.id,
            key,
            enabled=body.enabled,
            rules_status=body.rules_status,
            notification_class=body.notification_class,
        )
    except WorkflowNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ce workflow n'existe pas.") from None


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=300)
    role: Role = Role.EMPLOYEE


class UserActiveIn(BaseModel):
    active: bool


class AdminUserOut(UserOut):
    active: bool


def _admin_user_out(user: User) -> AdminUserOut:
    base = user_out(user)
    return AdminUserOut(**base.model_dump(), active=user.active)


@admin.get("/users", response_model=list[AdminUserOut])
def list_users(
    user: User = Depends(api_admin), app: Application = Depends(get_application)
) -> list[AdminUserOut]:
    del user
    return [_admin_user_out(u) for u in app.account_service.list_users()]


@admin.post(
    "/users",
    response_model=AdminUserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[same_origin],
)
def create_user(
    body: UserCreateIn,
    user: User = Depends(api_admin),
    app: Application = Depends(get_application),
) -> AdminUserOut:
    del user
    service: AccountService = app.account_service
    try:
        created = service.create_user(
            username=body.username,
            display_name=body.display_name,
            password=body.password,
            role=body.role,
        )
    except DuplicateUsernameError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cet identifiant existe déjà.") from None
    except WeakPasswordError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return _admin_user_out(created)


@admin.patch("/users/{user_id}", response_model=AdminUserOut, dependencies=[same_origin])
def set_user_active(
    user_id: int,
    body: UserActiveIn,
    admin_user: User = Depends(api_admin),
    app: Application = Depends(get_application),
) -> AdminUserOut:
    try:
        app.account_service.set_active(
            user_id=user_id, active=body.active, acting_admin_id=admin_user.id
        )
    except CannotDisableSelfError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Vous ne pouvez pas désactiver votre propre compte."
        ) from None
    for candidate in app.account_service.list_users():
        if candidate.id == user_id:
            return _admin_user_out(candidate)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")


router.include_router(admin)
