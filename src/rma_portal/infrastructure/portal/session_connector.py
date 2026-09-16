"""In-app OmegaFlow connection: the dashboard's "Se connecter"/"Reconnecter"
button.

Launches the same visible persistent Camoufox profile as
Configurer_Session_RMA.bat (``session_setup.launch_visible_browser_and_wait``),
as a background asyncio task owned by the running application instead of a
second console process. It acquires the same cross-process profile lock, so
it can never race the scheduler, a manual refresh, or the CLI fallback tool
-- whichever holds the lock wins, and the others skip safely.

When the employee closes the window, ``on_closed`` (in practice
``SyncAgreementQueue.execute``) runs immediately -- after the profile lock
has been released -- reusing the *existing* OmegaFlow authentication
detection rather than a second implementation, so the dashboard reflects
READY/AUTH_REQUIRED/ERROR without waiting for the next scheduled poll.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock
from rma_portal.infrastructure.portal.session_setup import (
    OpenAndWait,
    launch_visible_browser_and_wait,
)

logger = logging.getLogger(__name__)


class SessionConnector:
    def __init__(
        self,
        settings: Settings,
        on_closed: Callable[[], Awaitable[object]],
        *,
        open_and_wait: OpenAndWait = launch_visible_browser_and_wait,
    ) -> None:
        self._settings = settings
        self._on_closed = on_closed
        self._open_and_wait = open_and_wait
        self._task: asyncio.Task[None] | None = None

    @property
    def is_active(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> bool:
        """Start a connection window.

        Returns False and does nothing if one is already open -- a
        duplicate click (or a concurrent request) never opens a second
        browser.
        """
        if self.is_active:
            return False
        self._task = asyncio.create_task(self._run(), name="rma-portal-session-connect")
        return True

    async def shutdown(self) -> None:
        """Cancel an active connection task and wait for cleanup.

        Cancelling inside the ``async with AsyncCamoufox(...)`` block in
        ``launch_visible_browser_and_wait`` still runs that context
        manager's ``__aexit__`` (closing the browser) via Python's normal
        exception-propagation semantics, and the ``with
        acquire_profile_lock(...)`` around it still releases the lock.
        """
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        try:
            with acquire_profile_lock(self._settings.browser_lock_path):
                await self._open_and_wait(self._settings)
        except BrowserProfileLockedError:
            logger.info("connexion manuelle ignorée : profil de navigateur déjà utilisé")
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a bad launch crash the app
            logger.exception("échec inattendu lors de la connexion manuelle OmegaFlow")
            return

        # The lock above is released by this point, so the normal sync
        # (which acquires it itself, via the real PortalReader) runs cleanly.
        try:
            await self._on_closed()
        except Exception:  # noqa: BLE001 - never crash the connector on a sync failure
            logger.exception("échec de la synchronisation après fermeture du navigateur")
