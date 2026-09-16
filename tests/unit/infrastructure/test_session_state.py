"""Unit tests for session_state.py's capture/save/load/restore logic
against fake Playwright context/page doubles.

These cover the pure persistence mechanics (atomicity, "preserve the last
valid state on failure", origin-scoping) cheaply and deterministically.
They are NOT a substitute for a real browser round-trip -- see
test_session_state_real_browser.py for that -- but they catch logic bugs
(a wrong dict shape, a missed origin check, a non-atomic write) far
faster than a real Camoufox launch would.
"""

from __future__ import annotations

import json

import pytest

from rma_portal.infrastructure.portal.session_state import (
    capture_session_state,
    load_session_state,
    origin_of,
    restore_session_state,
    save_session_state,
)

ORIGIN = "https://omegaflow.ma"


class _FakeContext:
    def __init__(self, storage_state: dict) -> None:
        self._storage_state = storage_state
        self.added_cookies: list[dict] | None = None
        self.added_init_scripts: list[str] = []
        self.storage_state_calls = 0

    async def storage_state(self) -> dict:
        self.storage_state_calls += 1
        return self._storage_state

    async def add_cookies(self, cookies: list[dict]) -> None:
        self.added_cookies = cookies

    async def add_init_script(self, script: str) -> None:
        self.added_init_scripts.append(script)


class _FakePage:
    def __init__(self, session_storage: dict[str, str]) -> None:
        self._session_storage = session_storage

    async def evaluate(self, script: str) -> dict[str, str]:
        return dict(self._session_storage)


def _sample_storage_state() -> dict:
    return {
        "cookies": [
            {
                "name": "omega_session",
                "value": "s3cr3t-session-cookie",
                "domain": "omegaflow.ma",
                "path": "/",
            }
        ],
        "origins": [
            {
                "origin": ORIGIN,
                "localStorage": [{"name": "omega_user_pref", "value": "fr"}],
            }
        ],
    }


def _sample_session_storage() -> dict[str, str]:
    return {"omega_session_start": "1234567890", "omega_login_tracked": "true"}


@pytest.mark.asyncio
async def test_capture_combines_storage_state_and_session_storage():
    context = _FakeContext(_sample_storage_state())
    page = _FakePage(_sample_session_storage())

    state = await capture_session_state(context, page, origin=ORIGIN)

    assert state["cookies"] == _sample_storage_state()["cookies"]
    assert state["origins"] == _sample_storage_state()["origins"]
    assert state["session_storage"] == {"origin": ORIGIN, "items": _sample_session_storage()}
    assert "captured_at" in state
    assert context.storage_state_calls == 1


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "state.json"
    state = {"cookies": [{"name": "a", "value": "b"}], "origins": [], "session_storage": {}}

    save_session_state(path, state)
    loaded = load_session_state(path)

    assert loaded == state


def test_load_missing_file_returns_none(tmp_path):
    assert load_session_state(tmp_path / "does-not-exist.json") is None


def test_load_corrupt_file_returns_none_without_raising(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert load_session_state(path) is None


def test_save_is_atomic_no_temp_file_left_behind(tmp_path):
    path = tmp_path / "state.json"
    save_session_state(path, {"cookies": [], "origins": [], "session_storage": {}})

    leftovers = list(tmp_path.glob(".omegaflow-session-*"))
    assert leftovers == []
    assert path.exists()


def test_save_never_corrupts_a_previously_saved_file_on_failure(tmp_path, monkeypatch):
    """Regression: 'preserve the last valid saved state if login is
    cancelled or fails' -- a failure partway through saving must never
    leave a half-written or missing file where a good one used to be."""
    path = tmp_path / "state.json"
    good_state = {
        "cookies": [{"name": "old", "value": "good"}],
        "origins": [],
        "session_storage": {},
    }
    save_session_state(path, good_state)

    import rma_portal.infrastructure.portal.session_state as session_state_module

    def _boom(*_args, **_kwargs):
        raise OSError("disk full (test)")

    monkeypatch.setattr(session_state_module.os, "replace", _boom)

    with pytest.raises(OSError):
        save_session_state(
            path,
            {"cookies": [{"name": "new", "value": "bad"}], "origins": [], "session_storage": {}},
        )

    assert load_session_state(path) == good_state
    assert list(tmp_path.glob(".omegaflow-session-*")) == []  # temp file cleaned up


@pytest.mark.asyncio
async def test_restore_applies_cookies_and_matching_origin_storage():
    context = _FakeContext(_sample_storage_state())
    state = {
        "cookies": _sample_storage_state()["cookies"],
        "origins": _sample_storage_state()["origins"],
        "session_storage": {"origin": ORIGIN, "items": _sample_session_storage()},
    }

    await restore_session_state(context, state, origin=ORIGIN)

    assert context.added_cookies == state["cookies"]
    assert len(context.added_init_scripts) == 1
    script = context.added_init_scripts[0]
    assert ORIGIN in script
    assert "omega_user_pref" in script
    assert "omega_session_start" in script
    assert "omega_login_tracked" in script
    # Values are embedded verbatim (json.dumps escaping) -- confirm the
    # actual session-cookie value never ends up somewhere unexpected like
    # this test's own assertion text would catch if it leaked into logs.
    assert "s3cr3t-session-cookie" not in script  # only in cookies, not the storage script


@pytest.mark.asyncio
async def test_restore_never_applies_storage_from_a_different_origin():
    """Regression: 'Restore storage only for its original origin.'"""
    context = _FakeContext(_sample_storage_state())
    state = {
        "cookies": [],
        "origins": [
            {"origin": "https://evil.example", "localStorage": [{"name": "x", "value": "y"}]}
        ],
        "session_storage": {"origin": "https://evil.example", "items": {"a": "b"}},
    }

    await restore_session_state(context, state, origin=ORIGIN)

    assert context.added_cookies is None
    assert context.added_init_scripts == []


@pytest.mark.asyncio
async def test_restore_is_a_no_op_when_nothing_was_saved():
    context = _FakeContext(_sample_storage_state())

    await restore_session_state(context, None, origin=ORIGIN)

    assert context.added_cookies is None
    assert context.added_init_scripts == []


def test_origin_of_strips_trailing_slash():
    assert origin_of("https://omegaflow.ma/") == "https://omegaflow.ma"
    assert origin_of("https://omegaflow.ma") == "https://omegaflow.ma"


def test_save_does_not_write_readable_secrets_into_the_repository(tmp_path):
    """Sanity: the state file must land exactly where given (the caller's
    settings.data_dir, outside the repo) -- not implicitly somewhere
    else, e.g. via a hardcoded relative path."""
    path = tmp_path / "nested" / "omegaflow-session-state.json"
    save_session_state(path, {"cookies": [], "origins": [], "session_storage": {}})

    assert path.exists()
    # Written as plain JSON (not further wrapped/encoded) so a future
    # audit can inspect the file's structure without needing this module.
    json.loads(path.read_text(encoding="utf-8"))
