"""Configurer_Session_RMA.bat's flow (run_session_setup) after the refactor
that lets SessionConnector share ``launch_visible_browser_and_wait``.

Proves the .bat's underlying behavior still works: it still opens the
browser inside the profile lock, and still degrades gracefully (no crash,
a clear French message) when the profile is already in use -- e.g. by the
new in-app connector.
"""

from __future__ import annotations

import asyncio

import pytest
from filelock import FileLock

from rma_portal.config import Settings
from rma_portal.infrastructure.portal import session_setup
from rma_portal.infrastructure.portal.session_setup import run_session_setup


class _RecordingOpenAndWait:
    def __init__(self) -> None:
        self.calls = 0
        self.received_settings: list[Settings] = []

    async def __call__(self, settings: Settings, *, on_teardown_unconfirmed=None) -> None:
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


class _FakeEmitter:
    """A minimal stand-in for Playwright/Camoufox's Node-style event API
    (``.on(event, handler)``), enough to drive ``_wait_until_closed``
    without a real browser -- a synthetic local login fixture."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: object) -> None:
        for handler in list(self._handlers.get(event, ())):
            handler(*args)


class _FakePage(_FakeEmitter):
    async def goto(self, url: str, wait_until: str | None = None) -> None:
        return None


class _FakeContext(_FakeEmitter):
    def __init__(self, pages: list[_FakePage] | None = None) -> None:
        super().__init__()
        self.pages: list[_FakePage] = list(pages) if pages is not None else []

    def close_via_context_event(self) -> None:
        """The documented-happy-path close: the context itself emits "close"."""
        self.pages = []
        self.emit("close")

    def close_page_without_context_event(self, page: _FakePage) -> None:
        """Closes one specific page (out of possibly several open at once)
        without the context itself ever emitting "close"."""
        self.pages.remove(page)
        page.emit("close")

    def close_last_page_without_context_event(self) -> None:
        """Simulates what was observed live: the browser process/window
        tears down when the employee closes it, the last *page* fires
        "close", but the context's own "close" event never fires."""
        self.close_page_without_context_event(self.pages[-1])

    def vanish_silently(self) -> None:
        """No event at all fires (an event-emission quirk); only the
        pages list itself reflects the closure -- the bounded poll is the
        only thing that can ever notice this."""
        self.pages = []


@pytest.mark.asyncio
async def test_wait_until_closed_returns_immediately_for_an_already_closed_context():
    """Regression: the context closing before ``_wait_until_closed`` is even
    called (e.g. the employee closed the window very fast) used to hang
    forever -- the single "close" listener registered after the fact would
    never see an event that already happened."""
    context = _FakeContext(pages=[])  # already closed: no open pages

    await asyncio.wait_for(session_setup._wait_until_closed(context), timeout=0.5)


@pytest.mark.asyncio
async def test_wait_until_closed_resolves_on_the_context_close_event():
    context = _FakeContext(pages=[_FakePage()])

    async def close_soon() -> None:
        await asyncio.sleep(0)
        context.close_via_context_event()

    await asyncio.gather(
        asyncio.wait_for(session_setup._wait_until_closed(context), timeout=0.5),
        close_soon(),
    )


@pytest.mark.asyncio
async def test_wait_until_closed_resolves_when_only_the_last_page_closes():
    """Regression: the context's own "close" event never firing (observed
    live) must not hang this -- the last page closing is itself a robust
    signal."""
    context = _FakeContext(pages=[_FakePage()])

    async def close_soon() -> None:
        await asyncio.sleep(0)
        context.close_last_page_without_context_event()

    await asyncio.gather(
        asyncio.wait_for(session_setup._wait_until_closed(context), timeout=0.5),
        close_soon(),
    )


@pytest.mark.asyncio
async def test_wait_until_closed_does_not_finish_while_another_page_remains_open():
    """Regression: with two pages open (e.g. an extra tab the employee
    opened), closing just one of them must not end the wait -- the old
    per-page "close" listener resolved unconditionally on ANY page
    closing, even with ``context.pages`` still non-empty afterward. Only
    closing the *last* remaining page (or the context itself) may finish."""
    first_page, second_page = _FakePage(), _FakePage()
    context = _FakeContext(pages=[first_page, second_page])
    finished = False

    async def waiter() -> None:
        nonlocal finished
        await session_setup._wait_until_closed(context)
        finished = True

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    context.close_page_without_context_event(first_page)
    await asyncio.sleep(0.05)
    assert context.pages == [second_page]
    assert finished is False  # a page is still open -- must not have finished
    assert not task.done()

    context.close_page_without_context_event(second_page)
    await asyncio.wait_for(task, timeout=0.5)
    assert finished is True


@pytest.mark.asyncio
async def test_wait_until_closed_falls_back_to_polling_when_no_event_fires_at_all(monkeypatch):
    """Regression: if Camoufox/Playwright's "close" event never fires (either
    on the context or any page) this must still notice via the bounded
    poll of ``context.pages`` -- not hang indefinitely."""
    monkeypatch.setattr(session_setup, "_CLOSE_POLL_INTERVAL_SECONDS", 0.05)
    context = _FakeContext(pages=[_FakePage()])

    async def vanish_soon() -> None:
        await asyncio.sleep(0.02)
        context.vanish_silently()

    await asyncio.gather(
        asyncio.wait_for(session_setup._wait_until_closed(context), timeout=1.0),
        vanish_soon(),
    )


class _StallingManager:
    """Fakes AsyncCamoufox itself: opens fine, but its own close (__aexit__)
    hangs forever -- proves launch_visible_browser_and_wait's own bounded
    cleanup, not just _wait_until_closed's."""

    def __init__(self, context: _FakeContext, gate: asyncio.Event) -> None:
        self._context = context
        self._gate = gate

    async def __aenter__(self) -> _FakeContext:
        return self._context

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._gate.wait()


@pytest.mark.asyncio
async def test_launch_visible_browser_and_wait_raises_when_teardown_stalls(tmp_path, monkeypatch):
    """Regression: the visible login browser's own AsyncCamoufox teardown
    stalling must not hang launch_visible_browser_and_wait forever --
    bounded, independently of the employee's own (unbounded) wait for the
    window to close, this must raise BrowserTeardownError instead so the
    caller (SessionConnector) can end CONNECTING and mark the profile
    unavailable rather than silently releasing the lock."""
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    page = _FakePage()
    context = _FakeContext(pages=[page])
    gate = asyncio.Event()  # deliberately never set: teardown hangs
    monkeypatch.setattr(
        session_setup, "AsyncCamoufox", lambda **kwargs: _StallingManager(context, gate)
    )

    async def close_soon() -> None:
        await asyncio.sleep(0)
        context.close_via_context_event()

    marked: list[bool] = []
    with pytest.raises(session_setup.BrowserTeardownError):
        await asyncio.wait_for(
            asyncio.gather(
                session_setup.launch_visible_browser_and_wait(
                    settings,
                    teardown_timeout_seconds=0.05,
                    on_teardown_unconfirmed=lambda: marked.append(True),
                ),
                close_soon(),
            ),
            timeout=1.0,
        )
    assert marked == [True]


class _FailingGotoPage(_FakePage):
    async def goto(self, url: str, wait_until: str | None = None) -> None:
        raise RuntimeError("navigation failed (test)")


@pytest.mark.asyncio
async def test_launch_visible_browser_and_wait_marks_teardown_unconfirmed_on_navigation_failure(
    tmp_path, monkeypatch
):
    """Regression: on_teardown_unconfirmed must fire even when navigation
    itself failed before the window ever closed (body_succeeded stays
    False) and cleanup then also stalls -- the profile-lock protection
    must not depend on BrowserTeardownError being raised, which only
    happens when nothing else (here, the navigation failure) is already
    propagating. The original navigation error must still be what
    propagates, not BrowserTeardownError."""
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    context = _FakeContext(pages=[_FailingGotoPage()])
    gate = asyncio.Event()  # deliberately never set: teardown also stalls
    monkeypatch.setattr(
        session_setup, "AsyncCamoufox", lambda **kwargs: _StallingManager(context, gate)
    )

    marked: list[bool] = []
    with pytest.raises(RuntimeError, match="navigation failed"):
        await asyncio.wait_for(
            session_setup.launch_visible_browser_and_wait(
                settings,
                teardown_timeout_seconds=0.05,
                on_teardown_unconfirmed=lambda: marked.append(True),
            ),
            timeout=1.0,
        )

    assert marked == [True]


@pytest.mark.asyncio
async def test_launch_visible_browser_and_wait_marks_teardown_unconfirmed_when_cancelled(
    tmp_path, monkeypatch
):
    """Regression: cancellation (e.g. SessionConnector.shutdown() closing
    the app while the login window is still open) must still leave the
    profile marked unconfirmed if cleanup also stalls -- the original
    CancelledError must still be what propagates (never replaced by
    BrowserTeardownError, which would break callers relying on
    contextlib.suppress(asyncio.CancelledError)), but the lock protection
    must apply regardless."""
    settings = Settings(data_dir=tmp_path / "rma-portal-data")
    # The employee never closes the window, and the browser-manager
    # teardown also never completes -- both gates stay unset.
    context = _FakeContext(pages=[_FakePage()])
    teardown_gate = asyncio.Event()
    monkeypatch.setattr(
        session_setup, "AsyncCamoufox", lambda **kwargs: _StallingManager(context, teardown_gate)
    )

    marked: list[bool] = []
    task = asyncio.create_task(
        session_setup.launch_visible_browser_and_wait(
            settings,
            teardown_timeout_seconds=0.05,
            on_teardown_unconfirmed=lambda: marked.append(True),
        )
    )
    await asyncio.sleep(0)  # let it start waiting on _wait_until_closed
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)

    assert marked == [True]
