"""Test doubles for ``session_setup.OpenAndWait`` / ``SessionConnector``.

Uses an ``asyncio.Event`` (not a ``threading.Event``/``asyncio.to_thread``
blocking call) so that cancelling the task awaiting it -- e.g.
``SessionConnector.shutdown()`` -- actually interrupts it promptly.
``hold_open``/``close`` are still safe to call from a different thread than
the one running the coroutine (needed because
``starlette.testclient.TestClient`` runs the app on a persistent event loop
in a background thread, a ``BlockingPortal``, when used as
``with TestClient(app):``): they hop onto that loop via
``call_soon_threadsafe`` once it is known, or touch the event directly
before the coroutine has started (i.e. before there is a loop to hop onto).
"""

from __future__ import annotations

import asyncio

from rma_portal.config import Settings


class FakeOpenAndWait:
    def __init__(self) -> None:
        self.calls = 0
        self._closed = asyncio.Event()
        self._closed.set()  # default: "closes" instantly, a harmless no-op
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __call__(self, settings: Settings) -> None:
        self.calls += 1
        self._loop = asyncio.get_running_loop()
        await self._closed.wait()

    def hold_open(self) -> None:
        """The next (or current) call blocks until :meth:`close` is called.

        Call this before starting the connection -- at that point no
        coroutine is running yet, so touching the event directly is safe.
        """
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._closed.clear)
        else:
            self._closed.clear()

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._closed.set)
        else:
            self._closed.set()


class FakeVerifySession:
    """Test double for ``SessionConnector``'s ``verify_session`` callable.

    Same thread-hopping trick as :class:`FakeOpenAndWait`, for tests that
    need to hold the bounded post-login auth check open across a
    ``with TestClient(app):`` request/response boundary (e.g. to prove
    other routes stay responsive while it runs).
    """

    def __init__(self, *, result: bool = True) -> None:
        self.calls: list[float] = []
        self.result = result
        self._released = asyncio.Event()
        self._released.set()  # default: resolves instantly
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __call__(self, timeout_seconds: float) -> bool:
        self.calls.append(timeout_seconds)
        self._loop = asyncio.get_running_loop()
        await self._released.wait()
        return self.result

    def hold(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._released.clear)
        else:
            self._released.clear()

    def release(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._released.set)
        else:
            self._released.set()
