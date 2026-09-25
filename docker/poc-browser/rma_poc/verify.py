"""Headless verification of the saved OmegaFlow session.

Starts a brand-new headless browser process, restores the saved state with the
app's own ``restore_session_state``, navigates to the existing queue route and
requires positive evidence (``#view_1874``) for READY. Read-only: the saved
state is never modified here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import time
from pathlib import Path

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.infrastructure.portal.camoufox_reader import is_authenticated_view_present
from rma_portal.infrastructure.portal.parser import detect_auth_required
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock
from rma_portal.infrastructure.portal.session_state import (
    load_session_state,
    origin_of,
    restore_session_state,
)

from .common import BrowserSession, ExitCode, PocConfig, timed_stage

logger = logging.getLogger("rma_poc.verify")

POLL_INTERVAL_SECONDS = 0.5
NAVIGATION_TIMEOUT_MS = 30_000
PROBE_TIMEOUT_SECONDS = 5.0


async def _poll_for_result(page, deadline: float) -> ExitCode:
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):  # page mid-navigation -- retry
            html = await asyncio.wait_for(page.content(), timeout=PROBE_TIMEOUT_SECONDS)
            if detect_auth_required(html):
                return ExitCode.AUTH_REQUIRED
            if await asyncio.wait_for(
                is_authenticated_view_present(page), timeout=PROBE_TIMEOUT_SECONDS
            ):
                return ExitCode.OK
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return ExitCode.TIMEOUT


async def _verify(cfg: PocConfig, profile_dir: Path, timeout_s: float) -> ExitCode:
    state = load_session_state(cfg.state_path)
    if not state:
        logger.warning("no usable saved session state")
        return ExitCode.AUTH_REQUIRED

    deadline = time.monotonic() + timeout_s
    async with BrowserSession(cfg, headless=True, profile_dir=profile_dir) as session:
        context = session.context
        await restore_session_state(context, state, origin=origin_of(cfg.base_url))
        page = context.pages[0] if context.pages else await context.new_page()
        with timed_stage("verify_navigation"):
            await page.goto(
                cfg.start_route, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
            )
        with timed_stage("verify_auth_wait") as stage:
            result = await _poll_for_result(page, deadline)
            stage.outcome = result.name
        return result


async def run_verify(cfg: PocConfig, *, timeout_s: float, fresh_profile: bool) -> ExitCode:
    """``fresh_profile`` uses a throw-away profile so READY proves the saved
    state file alone is sufficient; otherwise the persistent profile is used,
    exactly like the application's reader."""
    try:
        with acquire_profile_lock(cfg.lock_path):
            if fresh_profile:
                with tempfile.TemporaryDirectory(prefix="rma-poc-verify-") as tmp:
                    return await _verify(cfg, Path(tmp), timeout_s)
            return await _verify(cfg, cfg.profile_dir, timeout_s)
    except BrowserProfileLockedError:
        logger.error("browser profile is busy (another poc command is running)")
        return ExitCode.PROFILE_BUSY
