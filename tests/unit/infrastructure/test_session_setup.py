"""Configurer_Session_RMA.bat's flow (run_session_setup) after the refactor
that lets SessionConnector share ``launch_visible_browser_and_wait``.

Proves the .bat's underlying behavior still works: it still opens the
browser inside the profile lock, and still degrades gracefully (no crash,
a clear French message) when the profile is already in use -- e.g. by the
new in-app connector.
"""

from __future__ import annotations

import pytest
from filelock import FileLock

from rma_portal.config import Settings
from rma_portal.infrastructure.portal.session_setup import run_session_setup


class _RecordingOpenAndWait:
    def __init__(self) -> None:
        self.calls = 0
        self.received_settings: list[Settings] = []

    async def __call__(self, settings: Settings) -> None:
        self.calls += 1
        self.received_settings.append(settings)


@pytest.mark.asyncio
async def test_run_session_setup_opens_the_browser_within_the_profile_lock(tmp_path):
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    fake = _RecordingOpenAndWait()

    await run_session_setup(settings, open_and_wait=fake)

    assert fake.calls == 1
    assert fake.received_settings == [settings]


@pytest.mark.asyncio
async def test_run_session_setup_degrades_gracefully_on_profile_lock_conflict(tmp_path, capsys):
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    settings.ensure_directories()
    lock = FileLock(str(settings.browser_lock_path), timeout=0)
    lock.acquire()
    try:
        fake = _RecordingOpenAndWait()

        await run_session_setup(settings, open_and_wait=fake)

        assert fake.calls == 0
        output = capsys.readouterr().out
        assert "déjà utilisé" in output
    finally:
        lock.release()
