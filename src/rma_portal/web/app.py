"""FastAPI application factory (the web layer's composition point).

``bootstrap.build_application`` constructs the ``Application`` (use cases +
infrastructure); this module only turns that into HTTP routes, templates
and the background poller lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rma_portal.bootstrap import Application, build_application
from rma_portal.infrastructure.scheduler.poller import PollScheduler
from rma_portal.infrastructure.security.sessions import SessionCodec
from rma_portal.web.routes import (
    admin_session,
    admin_users,
    auth,
    dashboard,
    dossier,
    health,
    session,
)

_WEB_DIR = Path(__file__).parent


def create_app(application: Application | None = None) -> FastAPI:
    application = application or build_application()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler = PollScheduler(
            application.sync_service, application.settings.poll_interval_seconds
        )
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await application.session_connector.shutdown()

    app = FastAPI(title="Portail RMA", lifespan=lifespan)
    app.state.application = application
    app.state.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.session_codec = SessionCodec(
        application.settings.session_secret, application.settings.session_lifetime_hours * 3600
    )
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(dossier.router)
    app.include_router(admin_users.router)
    app.include_router(admin_session.router)
    app.include_router(session.router)

    return app
