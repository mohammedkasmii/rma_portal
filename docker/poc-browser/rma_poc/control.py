"""Private control plane for the visible OmegaFlow login browser.

Only the API container can reach this service on the private Compose network.
Every non-health request requires a shared bearer token.  The controller owns
the in-process login task, so it never needs the Docker socket or a shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from .common import ExitCode, PocConfig, configure_logging
from .login import run_login

_TOKEN_ENV = "RMA_POC_CONTROL_TOKEN"
_LOGIN_TIMEOUT_SECONDS = 900.0


class ControlState(BaseModel):
    state: str
    started: bool = False
    result: str | None = None


class LoginController:
    def __init__(self, cfg: PocConfig) -> None:
        self._cfg = cfg
        self._task: asyncio.Task[ExitCode] | None = None
        self._result: str | None = None

    def state(self) -> ControlState:
        if self._task is not None and not self._task.done():
            return ControlState(state="CONNECTING")
        return ControlState(state="IDLE", result=self._result)

    def start(self) -> ControlState:
        if self._task is not None and not self._task.done():
            return ControlState(state="CONNECTING", started=False)
        self._result = None
        self._task = asyncio.create_task(self._run(), name="omegaflow-visible-login")
        return ControlState(state="CONNECTING", started=True)

    async def _run(self) -> ExitCode:
        try:
            code = await run_login(self._cfg, timeout_s=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            self._result = "CANCELLED"
            raise
        except Exception as exc:  # noqa: BLE001 - reported by type only, never logged with data
            self._result = type(exc).__name__
            return ExitCode.USAGE
        self._result = "CAPTURED" if code is ExitCode.OK else code.name
        return code

    async def shutdown(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task


def create_app(*, token: str | None = None, cfg: PocConfig | None = None) -> FastAPI:
    secret = token if token is not None else os.environ.get(_TOKEN_ENV, "")
    if len(secret) < 32:
        raise RuntimeError(f"{_TOKEN_ENV} must contain at least 32 characters")
    config = cfg or PocConfig.from_env()
    configure_logging(config.log_path)
    controller = LoginController(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await controller.shutdown()

    app = FastAPI(title="RMA browser control", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.controller = controller

    def authorize(authorization: str = Header(default="")) -> None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, secret):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", response_model=ControlState)
    def browser_status(authorization: str = Header(default="")) -> ControlState:
        authorize(authorization)
        return controller.state()

    @app.post("/login", response_model=ControlState, status_code=status.HTTP_202_ACCEPTED)
    async def start_login(authorization: str = Header(default="")) -> ControlState:
        authorize(authorization)
        return controller.start()

    return app
