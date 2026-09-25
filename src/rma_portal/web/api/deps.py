"""Dependencies shared by the JSON API.

The API answers with JSON status codes (401/403) instead of the redirects the
server-rendered pages use, and every mutation passes the same-origin check.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from rma_portal.application.work_service import WorkService
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import Role
from rma_portal.domain.models import User
from rma_portal.web.deps import check_same_origin, get_application, get_current_user

same_origin = Depends(check_same_origin)


def get_work_service(app: Application = Depends(get_application)) -> WorkService:
    return app.work_service


def api_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentification requise.")
    return user


def api_admin(user: User = Depends(api_user)) -> User:
    if user.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Réservé aux administrateurs.")
    return user


def live_session_state(app: Application) -> str | None:
    """CONNECTING/VERIFYING are in-process facts of the (Windows) login connector."""
    connector = app.session_connector
    if connector.is_active:
        return "CONNECTING"
    if connector.is_verifying:
        return "VERIFYING"
    return None
