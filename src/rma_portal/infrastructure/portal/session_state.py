"""Captures and restores the OmegaFlow browser session across Camoufox
process restarts.

The persistent Firefox profile alone does not carry a signed-in OmegaFlow
(Knack) session forward: by spec, ``sessionStorage`` does not survive a
full browser restart -- only cookies and ``localStorage`` do (and only if
the profile's SQLite databases flush cleanly, which a killed/crashed
process does not guarantee either). OmegaFlow's own authenticated view
render also depends on ``sessionStorage`` flags set at login time, so
closing the browser after a successful manual login was silently throwing
away exactly the state needed to stay authenticated -- the employee saw
"Session expirée" and the login form again on the very next attempt, even
though nothing was actually wrong with the account.

This mirrors mcma_agent's proven pattern (``LoginCapability.
perform_manual_login`` / ``context.storage_state()``, captured *before*
the browser closes) -- extended with an explicit ``sessionStorage``
capture, since Playwright's ``storage_state()`` only ever covers cookies
and ``localStorage``.

Playwright's ``launch_persistent_context`` (what Camoufox's
``persistent_context=True`` uses) does not accept a ``storage_state``
constructor argument the way a plain (non-persistent) ``browser.
new_context()`` does -- there is no built-in "restore this state into a
persistent profile" call. Restoring here is therefore two separate,
Playwright-supported operations: ``BrowserContext.add_cookies()`` for
cookies, and ``BrowserContext.add_init_script()`` (a script that runs
before the page's own scripts, on navigation) to seed ``localStorage``/
``sessionStorage`` -- guarded to only ever touch the exact captured
origin.

Every function here logs stage results and storage *counts* only --
never a cookie/storage name or value.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_VERSION = 1

_READ_SESSION_STORAGE_JS = """() => {
    const out = {};
    for (let i = 0; i < window.sessionStorage.length; i++) {
        const key = window.sessionStorage.key(i);
        out[key] = window.sessionStorage.getItem(key);
    }
    return out;
}"""


def origin_of(base_url: str) -> str:
    """Normalizes a configured base URL to the bare origin Playwright's
    ``storage_state()`` uses (scheme + host, no trailing slash/path) --
    callers pass this as ``capture_session_state``/``restore_session_state``'s
    ``origin`` argument."""
    return base_url.rstrip("/")


async def capture_session_state(context: Any, page: Any, *, origin: str) -> dict:
    """Snapshot cookies + localStorage (via Playwright's own
    ``storage_state()``) and sessionStorage (hand-rolled -- Playwright does
    not capture it) for the current page's origin.

    Called only after a caller has positively confirmed the authenticated
    OmegaFlow UI is showing -- never on a login form or a loading shell,
    so a rejected/incomplete login never gets captured.
    """
    storage = await context.storage_state()
    session_items = await page.evaluate(_READ_SESSION_STORAGE_JS)
    return {
        "version": _STATE_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "cookies": storage.get("cookies", []),
        "origins": storage.get("origins", []),
        "session_storage": {"origin": origin, "items": session_items},
    }


def save_session_state(path: Path, state: dict) -> None:
    """Atomic write: a temp file in the same directory, then
    ``os.replace()``. A failure here (disk full, permissions) never
    corrupts or truncates whatever was previously saved -- the old file,
    if any, is left exactly as it was; see ``load_session_state`` for the
    "preserve the last valid state" side of that guarantee.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".omegaflow-session-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise


def load_session_state(path: Path) -> dict | None:
    """None if there is nothing saved yet, or the saved file cannot be
    read/parsed -- callers treat that exactly like "nothing saved" (an
    unreadable file is never treated as an error worth failing a poll or
    a connect attempt over, and it is never deleted or overwritten here,
    so a transient read glitch cannot destroy a previously good save).
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(
            "échec de lecture de l'état de session OmegaFlow sauvegardé (%s)", type(exc).__name__
        )
        return None


async def restore_session_state(context: Any, state: dict | None, *, origin: str) -> None:
    """Explicitly restores cookies, then registers a one-time-per-origin
    init script that seeds localStorage/sessionStorage before the page's
    own scripts run on the next navigation -- a plain ``page.evaluate()``
    after the page has already loaded would risk losing the race against
    OmegaFlow's own bootstrap deciding "not authenticated" first.

    A no-op if there is nothing saved, or if the saved state was captured
    for a different origin than ``origin`` -- storage is restored only
    for its original origin, never applied elsewhere.
    """
    if not state:
        return

    cookies = state.get("cookies") or []
    if cookies:
        try:
            await context.add_cookies(cookies)
        except Exception as exc:  # noqa: BLE001 - best-effort, never blocks the caller
            logger.error("échec de la restauration des cookies OmegaFlow (%s)", type(exc).__name__)

    origin_entry = next((o for o in state.get("origins", []) if o.get("origin") == origin), None)
    local_items = origin_entry.get("localStorage", []) if origin_entry else []
    session_entry = state.get("session_storage") or {}
    session_items = session_entry.get("items", {}) if session_entry.get("origin") == origin else {}
    if not local_items and not session_items:
        return

    script = _build_restore_init_script(origin, local_items, session_items)
    try:
        await context.add_init_script(script)
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks the caller
        logger.error(
            "échec de la restauration du stockage local/session OmegaFlow (%s)", type(exc).__name__
        )
        return

    logger.info(
        "état de session OmegaFlow restauré cookies=%d local_storage=%d session_storage=%d",
        len(cookies),
        len(local_items),
        len(session_items),
    )


def _build_restore_init_script(origin: str, local_items: list[dict], session_items: dict) -> str:
    # The captured values are embedded as a JSON literal, never string-
    # concatenated into the script -- json.dumps() is the escaping.
    payload = json.dumps({"origin": origin, "local": local_items, "session": session_items})
    return (
        "(() => {"
        f"  const data = {payload};"
        "   if (window.location.origin !== data.origin) return;"
        "   for (const item of data.local) {"
        "     try { window.localStorage.setItem(item.name, item.value); } catch (e) {}"
        "   }"
        "   for (const key of Object.keys(data.session)) {"
        "     try { window.sessionStorage.setItem(key, data.session[key]); } catch (e) {}"
        "   }"
        "})();"
    )
