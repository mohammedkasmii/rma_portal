"""Cross-process lock protecting the persistent Camoufox profile directory.

Both the scheduled/manual poller and Configurer_Session_RMA.bat acquire the
same lock file before touching the profile, so the two can never launch a
browser against it concurrently. Whichever process loses the race gets a
:class:`rma_portal.application.dto.BrowserProfileLockedError` immediately
(no waiting) so a scheduled poll can skip this cycle safely instead of
blocking.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from rma_portal.application.dto import BrowserProfileLockedError


@contextmanager
def acquire_profile_lock(lock_path: Path) -> Generator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise BrowserProfileLockedError(
            "Le profil de navigateur OmegaFlow est occupé "
            "(configuration de session en cours)."
        ) from exc
    try:
        yield
    finally:
        lock.release()
