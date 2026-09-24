"""Shared PoC configuration, exit codes, logging and bounded browser lifecycle.

Nothing here logs cookie/storage names or values, HTML, screenshots, or URLs
with query strings -- counts and outcome names only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import os
import signal
import sys
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from types import TracebackType
from typing import Any

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

from rma_portal.config import Settings
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReader

logger = logging.getLogger("rma_poc")

STARTUP_TIMEOUT_SECONDS = 90.0
CLOSE_TIMEOUT_SECONDS = 30.0


class ExitCode(IntEnum):
    OK = 0  # login: session captured / verify: READY
    USAGE = 2  # bad configuration or environment (e.g. no DISPLAY)
    AUTH_REQUIRED = 10  # verify: login form / session revalidation / no saved state
    LOGIN_CANCELLED = 13  # login: browser closed before authentication was seen
    TIMEOUT = 20  # bounded wait expired
    BROWSER_ERROR = 21  # browser failed to start, crashed or errored
    CAPTURE_FAILED = 22  # authenticated, but state could not be captured/saved
    PROFILE_BUSY = 30  # another command holds the browser profile lock


@dataclass(frozen=True, slots=True)
class PocConfig:
    base_url: str
    start_route: str
    locale: str
    timezone_id: str
    camoufox_os: str
    window: tuple[int, int]
    profile_dir: Path
    state_path: Path
    lock_path: Path
    log_path: Path

    @classmethod
    def from_env(cls) -> PocConfig:
        defaults = Settings()  # single source of truth for the OmegaFlow URLs
        root = Path(os.environ.get("POC_DATA_ROOT", "/var/lib/rma-poc"))
        width, _, height = os.environ.get("POC_WINDOW", "1400x820").partition("x")
        return cls(
            base_url=os.environ.get("POC_OMEGAFLOW_BASE_URL", defaults.omegaflow_base_url),
            start_route=os.environ.get("POC_OMEGAFLOW_START_ROUTE", defaults.omegaflow_start_route),
            locale=os.environ.get("POC_LOCALE", defaults.portal_locale),
            timezone_id=os.environ.get("POC_TIMEZONE", defaults.portal_timezone),
            camoufox_os=os.environ.get("POC_CAMOUFOX_OS", "windows"),
            window=(int(width), int(height)),
            profile_dir=root / "profile" / "browser-profile",
            state_path=root / "state" / "omegaflow-session-state.json",
            lock_path=root / "state" / "browser-profile.lock",
            log_path=root / "state" / "logs" / "poc.log",
        )


def configure_logging(log_path: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        pass  # console logging alone is fine
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(fmt)
        root.addHandler(handler)


class _ReadOnlyGuard:
    """Applies the application's own read-only request policy verbatim.

    ``CamoufoxPortalReader._read_only_route`` only needs
    ``self.blocked_write_attempts``; borrowing it keeps a single definition of
    "read-only" instead of a second copy that could drift.
    """

    route = CamoufoxPortalReader._read_only_route

    def __init__(self) -> None:
        self.blocked_write_attempts: list[str] = []


def context_closed(context: Any) -> bool:
    """Same best-effort test as the app: no pages, or the connection is gone."""
    try:
        return not context.pages
    except Exception:  # noqa: BLE001 - any access failure means "gone"
        return True


def kill_descendants() -> int:
    """SIGKILL every process descended from this one (Linux /proc only).

    Last resort after a bounded browser close gave up. Scoped to our own
    process subtree, never a name-based kill.
    """
    if not sys.platform.startswith("linux"):
        return 0
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(entry.name))
        except OSError, ValueError, IndexError:
            continue
    victims: list[int] = []
    stack = [os.getpid()]
    while stack:
        for child in children.get(stack.pop(), []):
            victims.append(child)
            stack.append(child)
    for pid in victims:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    return len(victims)


class BrowserSession:
    """Camoufox persistent context with bounded startup and bounded cleanup.

    Correctness never depends on a browser "close" event: startup and close
    each have a hard timeout, and a stalled close ends in a subtree SIGKILL.
    """

    def __init__(self, cfg: PocConfig, *, headless: bool, profile_dir: Path) -> None:
        self._cfg = cfg
        self._headless = headless
        self._profile_dir = profile_dir
        self._manager: AsyncCamoufox | None = None
        self.context: Any = None
        self.guard = _ReadOnlyGuard()
        self.cleanup_forced = False

    async def __aenter__(self) -> BrowserSession:
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        extra: dict[str, Any] = {} if self._headless else {"window": self._cfg.window}
        self._manager = AsyncCamoufox(
            persistent_context=True,
            user_data_dir=str(self._profile_dir),
            headless=self._headless,
            os=self._cfg.camoufox_os,
            geoip=False,
            exclude_addons=[DefaultAddons.UBO],
            locale=self._cfg.locale,
            timezone_id=self._cfg.timezone_id,
            firefox_user_prefs={"network.cookie.cookieBehavior": 4},
            **extra,
        )
        try:
            self.context = await asyncio.wait_for(
                self._manager.__aenter__(), timeout=STARTUP_TIMEOUT_SECONDS
            )
            await self.context.route("**/*", self.guard.route)
        except BaseException:
            await self._close()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        manager, self._manager = self._manager, None
        if manager is None:
            return
        try:
            await asyncio.wait_for(
                manager.__aexit__(None, None, None), timeout=CLOSE_TIMEOUT_SECONDS
            )
        except BaseException as exc:  # noqa: BLE001 - bounded cleanup must never propagate
            killed = kill_descendants()
            self.cleanup_forced = True
            logger.warning(
                "browser close not confirmed (%s); killed %d child processes",
                type(exc).__name__,
                killed,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.context = None
            if self.guard.blocked_write_attempts:
                logger.warning(
                    "blocked non-read-only requests: %d", len(self.guard.blocked_write_attempts)
                )
