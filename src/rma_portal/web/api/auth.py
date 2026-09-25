"""Authentication endpoints: HTTP-only, same-site session cookie."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from rma_portal.bootstrap import Application
from rma_portal.domain.models import User
from rma_portal.infrastructure.security.sessions import SessionCodec
from rma_portal.web.api.deps import api_user, same_origin
from rma_portal.web.deps import get_application, get_session_codec

router = APIRouter(prefix="/auth", tags=["auth"])

_WINDOW_SECONDS = 300
_MAX_FAILURES = 8


class LoginThrottle:
    """Slows password guessing: too many failures per client and username get a 429."""

    def __init__(self, clock=time.monotonic) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._clock = clock

    def _prune(self, key: str) -> deque[float]:
        attempts = self._failures[key]
        cutoff = self._clock() - _WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= _MAX_FAILURES

    def record_failure(self, key: str) -> None:
        self._prune(key).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


_throttle = LoginThrottle()


def reset_login_throttle() -> None:
    """Test hook: forget every recorded failure."""
    _throttle._failures.clear()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=300)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


def user_out(user: User) -> UserOut:
    assert user.id is not None
    return UserOut(
        id=user.id, username=user.username, display_name=user.display_name, role=user.role.value
    )


@router.post("/login", response_model=UserOut, dependencies=[same_origin])
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    app: Application = Depends(get_application),
    codec: SessionCodec = Depends(get_session_codec),
) -> UserOut:
    client = request.client.host if request.client else "unknown"
    key = f"{client}|{body.username.strip().casefold()}"
    if _throttle.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives. Réessayez dans quelques minutes.",
        )
    user = app.account_service.authenticate(body.username, body.password)
    if user is None:
        _throttle.record_failure(key)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides ou compte désactivé."
        )
    _throttle.reset(key)
    response.set_cookie(
        app.settings.session_cookie_name,
        codec.encode(user.id),
        max_age=app.settings.session_lifetime_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=app.settings.cookie_secure,
        path="/",
    )
    return user_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[same_origin])
def logout(app: Application = Depends(get_application)) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(app.settings.session_cookie_name, path="/")
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(api_user)) -> UserOut:
    return user_out(user)
