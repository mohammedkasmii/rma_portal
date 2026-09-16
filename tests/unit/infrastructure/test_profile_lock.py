"""Regression tests for the "retain ownership on unconfirmed teardown"
behavior added to ``acquire_profile_lock``/``mark_profile_teardown_unconfirmed``.

These test the mechanism in isolation from Camoufox/Playwright entirely:
a caller that cannot confirm a browser's teardown finished must be able to
keep the profile lock held (never released, not even by filelock's own
``__del__``-triggered auto-release) so a fresh attempt fails fast with an
actionable message instead of racing a browser process that might still be
running -- until the application restarts.
"""

from __future__ import annotations

import gc

import pytest
from filelock import FileLock

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.infrastructure.portal.profile_lock import (
    acquire_profile_lock,
    mark_profile_teardown_unconfirmed,
)


def test_normal_use_releases_the_lock_on_exit(tmp_path):
    lock_path = tmp_path / "profile.lock"

    with acquire_profile_lock(lock_path):
        pass

    # A fresh acquisition must succeed -- the lock was actually released.
    with acquire_profile_lock(lock_path):
        pass


def test_marking_unconfirmed_prevents_release_on_exit(tmp_path):
    lock_path = tmp_path / "profile.lock"

    with acquire_profile_lock(lock_path) as lock:
        mark_profile_teardown_unconfirmed(lock_path, lock, "test teardown stall")

    # The lock must still be held: a fresh attempt fails fast.
    with (
        pytest.raises(BrowserProfileLockedError, match="indisponible"),
        acquire_profile_lock(lock_path),
    ):
        pass


def test_unconfirmed_teardown_message_is_actionable(tmp_path):
    lock_path = tmp_path / "profile.lock"
    with acquire_profile_lock(lock_path) as lock:
        mark_profile_teardown_unconfirmed(lock_path, lock, "fermeture du navigateur")

    with pytest.raises(BrowserProfileLockedError) as excinfo, acquire_profile_lock(lock_path):
        pass
    message = str(excinfo.value)
    assert "fermeture du navigateur" in message
    assert "Redémarrez" in message


def test_marked_lock_survives_garbage_collection(tmp_path):
    """Regression: filelock's BaseFileLock.__del__ force-releases a lock
    whose last reference is dropped -- without an explicit registry
    keeping `lock` referenced, a dropped local variable (e.g. once the
    reader/connector that held it goes out of scope) would silently
    auto-release the very lock we are trying to keep held."""
    lock_path = tmp_path / "profile.lock"

    def _mark_and_drop() -> None:
        cm = acquire_profile_lock(lock_path)
        lock = cm.__enter__()
        mark_profile_teardown_unconfirmed(lock_path, lock, "gc test")
        cm.__exit__(None, None, None)
        # `cm` and `lock` go out of scope here.

    _mark_and_drop()
    gc.collect()  # force any pending __del__ finalizers to run now

    with (
        pytest.raises(BrowserProfileLockedError, match="indisponible"),
        acquire_profile_lock(lock_path),
    ):
        pass


def test_a_second_filelock_instance_on_the_same_path_still_conflicts(tmp_path):
    """Sanity check underpinning the whole mechanism: even within the same
    process, a *different* FileLock instance pointed at an already-held
    path must fail to acquire (this is what actually keeps the OS-level
    lock effective once we stop calling .release() on the original)."""
    lock_path = tmp_path / "profile.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = FileLock(str(lock_path), timeout=0)
    held.acquire()
    try:
        with pytest.raises(BrowserProfileLockedError), acquire_profile_lock(lock_path):
            pass
    finally:
        held.release()
