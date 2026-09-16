from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from rma_portal.bootstrap import Application
from rma_portal.domain.enums import Role
from rma_portal.domain.models import User
from rma_portal.infrastructure.security.sessions import SessionCodec


def get_application(request: Request) -> Application:
    return request.app.state.application


def get_session_codec(request: Request) -> SessionCodec:
    return request.app.state.session_codec


def get_current_user(
    request: Request,
    app: Application = Depends(get_application),
    codec: SessionCodec = Depends(get_session_codec),
) -> User | None:
    token = request.cookies.get(app.settings.session_cookie_name)
    if not token:
        return None
    user_id = codec.decode(token)
    if user_id is None:
        return None
    with app.uow_factory() as uow:
        user = uow.users.get(user_id)
    if user is None or not user.active:
        return None
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role is not Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Réservé aux administrateurs.")
    return user


def check_same_origin(request: Request) -> None:
    """Reject cross-origin mutations (see docs/architecture.md section 9).

    The office LAN app runs on plain HTTP with no CSRF token infrastructure,
    so every state-changing request must carry an Origin (or, failing that,
    a Referer) header that matches this server's own origin.
    """
    header = request.headers.get("origin") or request.headers.get("referer")
    if not header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origine manquante.")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if not header.startswith(expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origine invalide.")
