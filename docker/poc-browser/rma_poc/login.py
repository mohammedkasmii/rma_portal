"""Visible OmegaFlow login, positive authentication detection, atomic capture.

The employee logs in by hand inside the noVNC desktop. Success requires
positive evidence that ``#view_1874`` rendered (the app's own
``is_authenticated_view_present``) on consecutive polls -- the mere absence of
a login form is never enough. Capture happens while the browser is still open
(sessionStorage does not survive a browser restart) and is written atomically,
so a failed/cancelled login leaves any earlier valid state untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.infrastructure.portal.camoufox_reader import is_authenticated_view_present
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock
from rma_portal.infrastructure.portal.session_state import (
    capture_session_state,
    origin_of,
    save_session_state,
)

from .common import BrowserSession, ExitCode, PocConfig, context_closed, timed_stage

logger = logging.getLogger("rma_poc.login")

POLL_INTERVAL_SECONDS = 0.5
PROBE_TIMEOUT_SECONDS = 5.0
REQUIRED_CONSECUTIVE_HITS = 3
SETTLE_TIMEOUT_MS = 5_000


class CaptureError(RuntimeError):
    pass


async def _authenticated_page(context: Any) -> Any | None:
    """The first open page showing the rendered authenticated view, else None."""
    for page in list(context.pages):
        with contextlib.suppress(Exception):  # mid-navigation or closing -- retry next poll
            if await asyncio.wait_for(
                is_authenticated_view_present(page), timeout=PROBE_TIMEOUT_SECONDS
            ):
                return page
    return None


async def wait_for_authentication(context: Any, timeout_s: float) -> Any | ExitCode:
    """Page once authentication is positively and stably detected; otherwise
    ``ExitCode.LOGIN_CANCELLED`` (window closed) or ``ExitCode.TIMEOUT``."""
    deadline = time.monotonic() + timeout_s
    hits = 0
    while time.monotonic() < deadline:
        if context_closed(context):
            return ExitCode.LOGIN_CANCELLED
        page = await _authenticated_page(context)
        hits = hits + 1 if page is not None else 0
        if page is not None and hits >= REQUIRED_CONSECUTIVE_HITS:
            return page
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return ExitCode.TIMEOUT


def _count_local_storage(state: dict) -> int:
    return sum(len(o.get("localStorage", [])) for o in state.get("origins", []))


def _fsync_path(path: os.PathLike[str] | str) -> None:
    """Best-effort durability after the atomic replace already succeeded
    (Windows cannot fsync a read-only descriptor; Linux can)."""
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


async def capture_and_save(context: Any, page: Any, cfg: PocConfig) -> dict[str, int]:
    """Capture -> validate -> atomic replace -> fsync. Raises ``CaptureError``
    (leaving the previous file untouched) if there is nothing worth saving."""
    with contextlib.suppress(Exception):  # best-effort: let post-login XHRs settle
        await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    if not await is_authenticated_view_present(page):
        raise CaptureError("authenticated view disappeared before capture")

    origin = origin_of(cfg.base_url)
    state = await capture_session_state(context, page, origin=origin)
    # Scope web storage to the OmegaFlow origin only (the restore path already
    # ignores anything else; this keeps it out of the file entirely).
    state["origins"] = [o for o in state.get("origins", []) if o.get("origin") == origin]

    counts = {
        "cookies": len(state.get("cookies", [])),
        "local_storage": _count_local_storage(state),
        "session_storage": len(state.get("session_storage", {}).get("items", {})),
    }
    if counts["cookies"] == 0 and counts["local_storage"] == 0 and counts["session_storage"] == 0:
        raise CaptureError("captured state is empty")

    save_session_state(cfg.state_path, state)  # temp file + os.replace()
    _fsync_path(cfg.state_path)
    _fsync_path(cfg.state_path.parent)
    return counts


async def run_login(cfg: PocConfig, *, timeout_s: float) -> ExitCode:
    if not os.environ.get("DISPLAY"):
        logger.error("DISPLAY is not set; run this inside the rma-poc-browser container")
        return ExitCode.USAGE

    try:
        with acquire_profile_lock(cfg.lock_path):
            async with BrowserSession(cfg, headless=False, profile_dir=cfg.profile_dir) as session:
                context = session.context
                page = context.pages[0] if context.pages else await context.new_page()
                with timed_stage("initial_navigation"):
                    await page.goto(cfg.start_route, wait_until="domcontentloaded", timeout=60_000)
                logger.info(
                    "visible browser open; log in via noVNC (waiting up to %.0fs)", timeout_s
                )

                with timed_stage("auth_wait") as stage:
                    outcome = await wait_for_authentication(context, timeout_s)
                    stage.outcome = (
                        outcome.name if isinstance(outcome, ExitCode) else "AUTHENTICATED"
                    )
                if isinstance(outcome, ExitCode):
                    logger.warning(
                        "login not completed: %s (saved state left untouched)", outcome.name
                    )
                    return outcome

                try:
                    with timed_stage("session_capture"):
                        counts = await capture_and_save(context, outcome, cfg)
                except CaptureError as exc:
                    logger.error("session capture refused: %s (saved state left untouched)", exc)
                    return ExitCode.CAPTURE_FAILED
                except Exception as exc:  # noqa: BLE001 - reported by type only
                    logger.error(
                        "session capture failed: %s (saved state left untouched)",
                        type(exc).__name__,
                    )
                    return ExitCode.CAPTURE_FAILED
                logger.info(
                    "session captured cookies=%d local_storage=%d session_storage=%d",
                    counts["cookies"],
                    counts["local_storage"],
                    counts["session_storage"],
                )
                return ExitCode.OK
    except BrowserProfileLockedError:
        logger.error("browser profile is busy (another poc command is running)")
        return ExitCode.PROFILE_BUSY
