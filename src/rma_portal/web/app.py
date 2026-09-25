"""FastAPI application factory (the web layer's composition point).

``bootstrap.build_application`` constructs the ``Application`` (use cases +
infrastructure); this module only turns that into HTTP routes, templates
and the background poller lifecycle.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rma_portal.bootstrap import Application, build_application
from rma_portal.infrastructure.scheduler.poller import PollScheduler
from rma_portal.infrastructure.security.sessions import SessionCodec
from rma_portal.web.api import router as api_router
from rma_portal.web.routes import (
    admin_session,
    admin_users,
    auth,
    dashboard,
    dossier,
    health,
    session,
)

logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent
_SLOW_REQUEST_SECONDS = 1.0


def _local_datetime_filter(tz: ZoneInfo):
    def _format(value: datetime | None, pattern: str = "%d/%m/%Y %H:%M") -> str:
        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(tz).strftime(pattern)

    return _format


def _route_template(request: Request) -> str:
    """The matched route's path *template* (e.g. ``/dossiers/{dossier_id}``)
    -- never the resolved path, which can carry a dossier id, and never the
    query string, which is excluded entirely from web-request logging."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template or request.url.path


def create_app(application: Application | None = None) -> FastAPI:
    application = application or build_application()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler = PollScheduler(
            application.sync_service, application.settings.poll_interval_seconds
        )
        # The production stack runs the scheduler in its own worker process.
        if application.settings.run_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await application.session_connector.shutdown()

    app = FastAPI(title="Portail RMA", lifespan=lifespan)
    app.state.application = application
    app.state.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.templates.env.filters["local_dt"] = _local_datetime_filter(
        ZoneInfo(application.settings.portal_timezone)
    )
    app.state.session_codec = SessionCodec(
        application.settings.session_secret, application.settings.session_lifetime_hours * 3600
    )
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.middleware("http")
    async def _log_slow_or_failed_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "web_request method=%s route=%s status=ERROR duration_ms=%.1f",
                request.method,
                _route_template(request),
                (time.perf_counter() - started) * 1000,
                exc_info=True,
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400 or duration_ms > _SLOW_REQUEST_SECONDS * 1000:
            log = logger.error if response.status_code >= 500 else logger.warning
            log(
                "web_request method=%s route=%s status=%d duration_ms=%.1f",
                request.method,
                _route_template(request),
                response.status_code,
                duration_ms,
            )
        return response

    app.include_router(health.router)
    app.include_router(api_router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(dossier.router)
    app.include_router(admin_users.router)
    app.include_router(admin_session.router)
    app.include_router(session.router)

    return app
