"""Shared view-model for the OmegaFlow session card.

Used both by the dashboard (full page and its 30s HTMX refresh) and by
``POST /session/connect`` (which returns just this fragment), so the two
can never disagree about what state is currently shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rma_portal.bootstrap import Application
from rma_portal.domain.models import PortalAccount

# CONNECTING/VERIFYING are live, in-process facts (is the login browser
# open? is the bounded post-login auth check running?), never persisted --
# if the server restarts mid-connection there is no window/check anymore, so
# there is nothing to remember. UNKNOWN/READY/AUTH_REQUIRED/ERROR come
# straight from the persisted SessionStatus.
SESSION_STATES = ("UNKNOWN", "CONNECTING", "VERIFYING", "READY", "AUTH_REQUIRED", "ERROR")


@dataclass(frozen=True, slots=True)
class SessionView:
    state: str
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    syncing: bool


def build_session_view(app: Application, portal_account: PortalAccount | None) -> SessionView:
    if app.session_connector.is_active:
        state = "CONNECTING"
    elif app.session_connector.is_verifying:
        state = "VERIFYING"
    elif portal_account is not None:
        state = portal_account.session_status.value
    else:
        state = "UNKNOWN"
    return SessionView(
        state=state,
        last_poll_at=portal_account.last_poll_at if portal_account else None,
        last_success_at=portal_account.last_success_at if portal_account else None,
        last_error=portal_account.last_error if portal_account else None,
        # Separate from `state`: the normal synchronization that follows a
        # successful connect-triggered verification (or a scheduled/manual
        # poll) runs as its own phase and must not keep the session card
        # itself on a non-actionable state while it progresses.
        syncing=app.sync_service.is_running,
    )
