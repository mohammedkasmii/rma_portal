"""Real-Camoufox, real-process round-trip test for OmegaFlow session
persistence (login -> capture -> close process -> reopen -> restore ->
authenticated page).

This is deliberately NOT mock-only: the reported bug ("Session expirée"
immediately after a successful manual login) is specifically about state
that only a real browser process boundary can lose -- a fake Page/Context
double, however careful, cannot demonstrate that sessionStorage does not
survive a real Firefox process exiting while cookies/localStorage (mostly)
do. tests/unit/infrastructure/test_session_state.py covers the pure
capture/save/load/restore logic quickly and deterministically; this file
proves the actual persistence guarantee end-to-end.

Runs a tiny local HTTP server (the "synthetic local login fixture") that
plays the part of OmegaFlow closely enough to matter: ``/`` renders the
authenticated marker (#view_1874) only when a session cookie, a
localStorage key, AND a sessionStorage flag are ALL present -- and, like
the real portal (the employee saw the login form again, not an automatic
recovery), never tries to self-heal by redirecting to ``/authenticate``
on its own. A pass here is therefore not explainable by any single one of
the three restore paths alone, nor by anything other than restoration
actually working -- without it, phase 2 below hits a wall exactly the
way the real bug report describes: the login form again.

Uses ``headless=True`` (the actual login flow is ``headless=False``, but
that has no bearing on whether cookies/localStorage/sessionStorage
persist -- this only avoids opening a real, visible browser window during
an unattended test run).
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading
from collections.abc import Iterator

import pytest
from camoufox.async_api import AsyncCamoufox

from rma_portal.config import Settings
from rma_portal.infrastructure.portal.camoufox_reader import (
    CamoufoxPortalReader,
    is_authenticated_view_present,
)
from rma_portal.infrastructure.portal.session_state import (
    capture_session_state,
    load_session_state,
    origin_of,
    restore_session_state,
    save_session_state,
)

_ROOT_PAGE_HTML = b"""<!doctype html>
<html><body>
<div id="root">Chargement...</div>
<script>
(function () {
    var hasCookie = document.cookie.indexOf('omega_session=') !== -1;
    var hasLocal = window.localStorage.getItem('omega_user_pref') !== null;
    var hasSession = window.sessionStorage.getItem('omega_login_tracked') === 'true';
    if (hasCookie && hasLocal && hasSession) {
        var view = document.createElement('div');
        view.id = 'view_1874';
        view.textContent = 'Dossiers';
        document.getElementById('root').replaceWith(view);
    }
    // Otherwise: stays on the neutral "Chargement..." placeholder --
    // like the real portal in the bug report, there is no self-healing
    // redirect back to /authenticate here.
})();
</script>
</body></html>"""

_AUTHENTICATE_PAGE_HTML = b"""<!doctype html>
<html><body>
<script>
window.localStorage.setItem('omega_user_pref', 'fr');
window.sessionStorage.setItem('omega_session_start', String(Date.now()));
window.sessionStorage.setItem('omega_login_tracked', 'true');
window.location.href = '/';
</script>
</body></html>"""


class _SyntheticOmegaFlowHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format_str: str, *args: object) -> None:  # noqa: A002
        pass  # keep pytest output quiet

    def do_GET(self) -> None:  # noqa: N802 - stdlib-mandated name
        if self.path.startswith("/authenticate"):
            # Stands in for the employee completing a real login (form +
            # OTP, in OmegaFlow's case) -- the synthetic fixture's one
            # simplification is treating "reached this route" as that
            # having happened, exactly as mcma_agent's LoginCapability
            # never simulates credential entry either, only polls the
            # resulting logged-in markers.
            self._respond(_AUTHENTICATE_PAGE_HTML, set_session_cookie=True)
        else:
            # No self-healing redirect: like the real portal in the bug
            # report, an unauthenticated request here shows the login
            # form and stays there -- reaching /authenticate again is a
            # deliberate, separate action this test only ever takes once.
            self._respond(_ROOT_PAGE_HTML, set_session_cookie=False)

    def _respond(self, body: bytes, *, set_session_cookie: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if set_session_cookie:
            # No Max-Age/Expires -- a genuine session cookie.
            self.send_header("Set-Cookie", "omega_session=synthetic-session-token; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def synthetic_omegaflow_server() -> Iterator[str]:
    server = socketserver.TCPServer(("127.0.0.1", 0), _SyntheticOmegaFlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


async def _poll_until_authenticated(page, *, interval_seconds: float = 0.2) -> None:
    while not await is_authenticated_view_present(page):
        await asyncio.sleep(interval_seconds)


@pytest.mark.asyncio
async def test_real_camoufox_login_capture_close_reopen_restore_roundtrip(
    tmp_path, synthetic_omegaflow_server
):
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    origin = origin_of(synthetic_omegaflow_server)
    start_route = f"{origin}/"
    profile_dir = settings.browser_profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    # --- Phase 1: a real Camoufox process "logs in" (reaches
    # /authenticate, the fixture's stand-in for a completed human login),
    # positively detects authentication on /, captures, and saves -- then
    # the *process itself* exits (the persistent context's __aexit__
    # really tears the browser down; nothing here just drops a Python
    # reference).
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile_dir),
        headless=True,
        os="windows",
        geoip=False,
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"{origin}/authenticate", wait_until="domcontentloaded")
        await asyncio.wait_for(_poll_until_authenticated(page), timeout=15.0)

        state = await capture_session_state(context, page, origin=origin)
        save_session_state(settings.session_state_path, state)

    # The saved state actually contains all three storage kinds -- the
    # phase 2 pass below is not explainable by any single one alone.
    saved = json.loads(settings.session_state_path.read_text(encoding="utf-8"))
    assert any(c["name"] == "omega_session" for c in saved["cookies"])
    origin_entry = next(o for o in saved["origins"] if o["origin"] == origin)
    assert any(item["name"] == "omega_user_pref" for item in origin_entry["localStorage"])
    assert saved["session_storage"]["origin"] == origin
    assert saved["session_storage"]["items"]["omega_login_tracked"] == "true"
    assert "omega_session_start" in saved["session_storage"]["items"]

    # --- Phase 2: a brand-new Camoufox process (fresh Playwright/Firefox
    # connection, same on-disk profile) restores the saved state and must
    # see the authenticated page on the very first request to `/` --
    # which, unlike /authenticate, never sets anything itself. Without
    # restoration this would show the login form and stay there exactly
    # like the reported bug, so a bounded wait_for is what turns that
    # into a clear, fast test failure instead of a real hang.
    reload_state = load_session_state(settings.session_state_path)
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile_dir),
        headless=True,
        os="windows",
        geoip=False,
    ) as context:
        await restore_session_state(context, reload_state, origin=origin)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(start_route, wait_until="domcontentloaded")
        await asyncio.wait_for(_poll_until_authenticated(page), timeout=15.0)


@pytest.mark.asyncio
async def test_real_camoufox_reader_restores_and_verifies_authenticated(
    tmp_path, synthetic_omegaflow_server
):
    """Same round-trip, but through the actual production restore path:
    CamoufoxPortalReader.__aenter__ (not this test calling
    restore_session_state directly) followed by the real, reused
    verify_authenticated() -- the exact method SyncAgreementQueue.
    verify_session calls in production."""
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    origin = origin_of(synthetic_omegaflow_server)
    start_route = f"{origin}/"
    profile_dir = settings.browser_profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile_dir),
        headless=True,
        os="windows",
        geoip=False,
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"{origin}/authenticate", wait_until="domcontentloaded")
        await asyncio.wait_for(_poll_until_authenticated(page), timeout=15.0)
        state = await capture_session_state(context, page, origin=origin)
        save_session_state(settings.session_state_path, state)

    reader = CamoufoxPortalReader(
        profile_dir=profile_dir,
        lock_path=settings.browser_lock_path,
        start_route=start_route,
        base_url=synthetic_omegaflow_server,
        timezone_id=settings.portal_timezone,
        session_state_path=settings.session_state_path,
        locale=settings.portal_locale,
        headless=True,
    )
    async with reader as r:
        await asyncio.wait_for(r.verify_authenticated(), timeout=15.0)  # must not raise/hang
