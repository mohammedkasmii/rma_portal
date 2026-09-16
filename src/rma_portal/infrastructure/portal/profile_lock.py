"""Cross-process lock protecting the persistent Camoufox profile directory.

Both the scheduled/manual poller and Configurer_Session_RMA.bat acquire the
same lock file before touching the profile, so the two can never launch a
browser against it concurrently. Whichever process loses the race gets a
:class:`rma_portal.application.dto.BrowserProfileLockedError` immediately
(no waiting) so a scheduled poll can skip this cycle safely instead of
blocking.

If a browser's teardown cannot be *confirmed* to have finished (a stalled
``__aexit__``/``AsyncCamoufox`` close, bounded and given up on by the
caller), releasing this lock would let a fresh launch race a browser
process that might still be running against the same profile directory --
so callers that hit that situation call :func:`mark_profile_teardown_unconfirmed`
instead of letting the lock release normally. The profile then stays
unavailable (a fast, actionable :class:`BrowserProfileLockedError` on every
further attempt) until the application restarts, the only way this ever
recovers -- deliberately: attempting to detect or kill the underlying
browser process ourselves risks terminating something unrelated.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from rma_portal.application.dto import BrowserProfileLockedError


@dataclass
class _UnconfirmedTeardown:
    """Deliberately keeps ``lock`` referenced: py-filelock's own
    ``BaseFileLock.__del__`` force-releases a lock whose last reference is
    dropped ("so a dropped reference never leaks a held lock"), which would
    silently defeat the whole point here. Holding it in this module-level
    registry is what keeps the OS-level lock genuinely held until restart.
    """

    lock: FileLock
    reason: str
    since: datetime


_unconfirmed: dict[str, _UnconfirmedTeardown] = {}


def mark_profile_teardown_unconfirmed(lock_path: Path, lock: FileLock, reason: str) -> None:
    """Record that ``lock_path``'s previous browser session did not confirm
    a clean close, and keep ``lock`` held rather than released.

    ``lock`` must be the exact :class:`FileLock` a prior
    :func:`acquire_profile_lock` call yielded -- releasing that specific
    object is the only thing that actually frees the OS-level lock, so
    holding a reference to it here (instead of a bare marker) is what
    prevents a silent auto-release.
    """
    _unconfirmed[str(lock_path)] = _UnconfirmedTeardown(
        lock=lock, reason=reason, since=datetime.now(UTC)
    )


def _unconfirmed_teardown_message(lock_path: Path) -> str | None:
    entry = _unconfirmed.get(str(lock_path))
    if entry is None:
        return None
    return (
        "Le profil de navigateur OmegaFlow est indisponible : la fermeture du "
        f"précédent navigateur n'a pas été confirmée ({entry.reason}, depuis "
        f"{entry.since.strftime('%d/%m/%Y %H:%M')} UTC). "
        "Redémarrez le Portail RMA pour réessayer."
    )


@contextmanager
def acquire_profile_lock(lock_path: Path) -> Generator[FileLock]:
    stuck = _unconfirmed_teardown_message(lock_path)
    if stuck is not None:
        raise BrowserProfileLockedError(stuck)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise BrowserProfileLockedError(
            "Le profil de navigateur OmegaFlow est occupé (configuration de session en cours)."
        ) from exc
    try:
        yield lock
    finally:
        # A caller that hit an unconfirmed teardown during this same `with`
        # block calls mark_profile_teardown_unconfirmed *before* exiting --
        # checking again here (rather than releasing unconditionally) is
        # what makes that effective regardless of how this generator is
        # finalized (explicit __exit__, or an implicit GC-triggered close()
        # if a caller ever dropped it without one).
        if str(lock_path) not in _unconfirmed:
            lock.release()
