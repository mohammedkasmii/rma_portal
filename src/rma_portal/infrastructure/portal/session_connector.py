"""In-app OmegaFlow connection: the dashboard's "Se connecter"/"Reconnecter"
button.

Launches the same visible persistent Camoufox profile as
Configurer_Session_RMA.bat (``session_setup.launch_visible_browser_and_wait``),
as a background asyncio task owned by the running application instead of a
second console process. It acquires the same cross-process profile lock, so
it can never race the scheduler, a manual refresh, or the CLI fallback tool
-- whichever holds the lock wins, and the others skip safely.

Three phases run one after another, each its own background task so the
dashboard can tell them apart instead of one long opaque "CONNECTING":

1. **connecting** (``is_active``) -- the visible login browser is open,
   waiting for the employee to close it. Bounded only by the employee,
   never a timer, but the *cleanup* that follows the window closing is
   itself bounded (see ``session_setup.launch_visible_browser_and_wait``).
   Ends the moment the window closes and cleanup finishes or gives up --
   it does *not* include what happens next. A cleanup that cannot be
   confirmed never releases the profile lock (see
   ``profile_lock.mark_profile_teardown_unconfirmed``), so the profile
   stays unavailable until the application restarts rather than risking a
   fresh launch racing a browser that might still be running.
2. **verifying** (``is_verifying``) -- a short, bounded, read-only check
   (``verify_session``, in practice ``SyncWorkflows.verify_session``)
   that the saved profile is still authenticated. A successful check marks
   the account READY immediately, without waiting for the full dossier
   baseline/enrichment -- that runs next, as its own phase.
3. The normal synchronization (``run_sync``, in practice
   ``SyncWorkflows.execute``) runs as a fire-and-forget task after a
   successful check. It is *not* part of ``is_active``/``is_verifying``, so
   it never keeps the dashboard on a stuck "CONNECTING"/"VERIFYING"; its own
   progress is ``SyncWorkflows.is_running``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from rma_portal.application.dto import BrowserProfileLockedError, BrowserTeardownError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.profile_lock import (
    acquire_profile_lock,
    mark_profile_teardown_unconfirmed,
)
from rma_portal.infrastructure.portal.session_setup import (
    OpenAndWait,
    launch_visible_browser_and_wait,
)
from rma_portal.observability import ensure_operation_context, new_operation_id, operation_context

logger = logging.getLogger(__name__)

VerifySession = Callable[[float], Awaitable[bool]]
RunSync = Callable[[], Awaitable[object]]
MarkLoginTeardownFailed = Callable[[str], Awaitable[object]]

_DEFAULT_VERIFY_TIMEOUT_SECONDS = 45.0

_LOGIN_TEARDOWN_FAILED_MESSAGE = (
    "La fenêtre de connexion OmegaFlow s'est fermée, mais son nettoyage n'a pas pu être "
    "confirmé ; le profil reste indisponible jusqu'au redémarrage du Portail RMA."
)


class SessionConnector:
    def __init__(
        self,
        settings: Settings,
        *,
        verify_session: VerifySession,
        run_sync: RunSync,
        mark_login_teardown_failed: MarkLoginTeardownFailed,
        is_sync_running: Callable[[], bool] = lambda: False,
        verify_timeout_seconds: float = _DEFAULT_VERIFY_TIMEOUT_SECONDS,
        open_and_wait: OpenAndWait = launch_visible_browser_and_wait,
    ) -> None:
        self._settings = settings
        self._verify_session = verify_session
        self._run_sync = run_sync
        self._mark_login_teardown_failed = mark_login_teardown_failed
        self._is_sync_running = is_sync_running
        self._verify_timeout_seconds = verify_timeout_seconds
        self._open_and_wait = open_and_wait
        self._login_task: asyncio.Task[None] | None = None
        self._verify_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self.last_start_blocked_by_sync = False

    @property
    def is_active(self) -> bool:
        """True only while the visible login browser is open (CONNECTING)."""
        return self._login_task is not None and not self._login_task.done()

    @property
    def is_verifying(self) -> bool:
        """True only during the short bounded post-login auth check (VERIFYING)."""
        return self._verify_task is not None and not self._verify_task.done()

    def start(self) -> bool:
        """Start a connection window.

        Returns False and does nothing if a window is already open or
        being verified (``is_active``/``is_verifying`` -- the dashboard
        already shows CONNECTING/VERIFYING for that, a duplicate click or
        concurrent request never opens a second browser). Also returns
        False, without ever creating ``_login_task``, whenever
        ``is_sync_running()`` reports a synchronization currently holds the
        profile lock -- the scheduler, a manual refresh, or the sync this
        same connector triggers after a successful login all go through
        the one shared ``SyncWorkflows.execute()``, so this one check
        covers all three. ``_run_login`` would fail on
        ``BrowserProfileLockedError`` moments later anyway (the lock is
        acquired inside it, not here), but checking first means
        ``is_active`` never flips true for a launch that was never going
        to happen -- the dashboard would otherwise briefly claim a
        connection window is open. ``last_start_blocked_by_sync`` records
        that specific reason, for the caller to show a more targeted
        message than "already running".
        """
        self.last_start_blocked_by_sync = False
        if self.is_active or self.is_verifying:
            return False
        if self._is_sync_running():
            self.last_start_blocked_by_sync = True
            return False
        self._login_task = asyncio.create_task(self._run_login(), name="rma-portal-session-connect")
        return True

    async def shutdown(self) -> None:
        """Cancel any in-flight phase and wait for cleanup.

        Cancelling ``launch_visible_browser_and_wait`` mid-flight still runs
        its own bounded browser close via Python's normal exception-
        propagation semantics, and the ``with acquire_profile_lock(...)``
        around it still releases the lock afterward -- unless that bounded
        close itself also fails to confirm within its own bound, in which
        case the profile is deliberately left unavailable rather than
        risking a fresh launch racing a browser that might still be
        running (see ``launch_visible_browser_and_wait``'s docstring).
        Awaiting each cancelled task here (instead of firing-and-forgetting)
        is what keeps shutdown free of "Task was destroyed but it is
        pending" warnings.
        """
        for attr in ("_login_task", "_verify_task", "_sync_task"):
            task: asyncio.Task[None] | None = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _run_login(self) -> None:
        # Mints the correlation ID for this whole connection attempt --
        # login, verification and the sync it triggers all inherit it
        # (asyncio.create_task copies the current contextvars context),
        # except the sync itself, which always mints its own fresh
        # "sync-*" ID (see SyncWorkflows.execute) since it is its own
        # tracked operation with its own poll_runs row.
        with operation_context(new_operation_id("conn")):
            started = time.perf_counter()
            try:
                with acquire_profile_lock(self._settings.browser_lock_path) as lock:
                    logger.info("stage=connection_login outcome=START")
                    try:
                        await self._open_and_wait(
                            self._settings,
                            # Called synchronously whenever cleanup cannot be
                            # confirmed -- whether the window closed normally
                            # and only cleanup itself stalled (the
                            # BrowserTeardownError case below), navigation
                            # failed before that, or this whole method is
                            # itself being cancelled (e.g. app shutdown). Never
                            # let the `with` block above release the lock in
                            # any of those cases: a fresh attempt could
                            # otherwise race a browser process that might
                            # still be running against this profile.
                            on_teardown_unconfirmed=lambda: mark_profile_teardown_unconfirmed(
                                self._settings.browser_lock_path,
                                lock,
                                "fermeture de la fenêtre de connexion",
                            ),
                        )
                    except BrowserTeardownError:
                        logger.error(
                            "stage=connection_login outcome=FAILED elapsed_ms=%.1f",
                            (time.perf_counter() - started) * 1000,
                        )
                        # Without this, build_session_view falls back to
                        # whatever READY/UNKNOWN state was persisted from
                        # before this attempt -- the dashboard would show no
                        # sign anything went wrong at all.
                        await self._mark_login_teardown_failed(_LOGIN_TEARDOWN_FAILED_MESSAGE)
                        return
                    logger.info(
                        "login window closed elapsed_ms=%.1f",
                        (time.perf_counter() - started) * 1000,
                    )
            except BrowserProfileLockedError:
                logger.info("connexion manuelle ignorée : profil de navigateur déjà utilisé")
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let a bad launch crash the app
                logger.exception("échec inattendu lors de la connexion manuelle OmegaFlow")
                return
            logger.info(
                "stage=connection_login outcome=OK elapsed_ms=%.1f",
                (time.perf_counter() - started) * 1000,
            )

            self._verify_task = asyncio.create_task(
                self._run_verify(), name="rma-portal-session-verify"
            )

    async def _run_verify(self) -> None:
        with ensure_operation_context("verify"):
            started = time.perf_counter()
            logger.info("stage=connection_verify outcome=START")
            try:
                verified = await self._verify_session(self._verify_timeout_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - verify_session already reports failures itself
                logger.exception("échec inattendu lors de la vérification de la session")
                verified = False
            logger.info(
                "stage=connection_verify outcome=%s elapsed_ms=%.1f",
                "OK" if verified else "FAILED",
                (time.perf_counter() - started) * 1000,
            )
            if verified:
                self._sync_task = asyncio.create_task(
                    self._run_sync_task(), name="rma-portal-session-sync"
                )

    async def _run_sync_task(self) -> None:
        started = time.perf_counter()
        logger.info("synchronization started")
        try:
            await self._run_sync()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never crash the connector on a sync failure
            logger.exception("échec de la synchronisation après vérification de la session")
        logger.info("synchronization finished elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
