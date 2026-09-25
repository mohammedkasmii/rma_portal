"""Manual OmegaFlow session connection.

``launch_visible_browser_and_wait`` is the shared core: open a *visible*
persistent Camoufox profile so an employee can log in and pass any
session-validation step by hand, then block until they close the window.
It never reads, asks for, or stores the OmegaFlow password -- see
docs/architecture.md.

Two callers share it:

- ``run_session_setup`` -- the blocking CLI flow behind
  Configurer_Session_RMA.bat. This is now a **fallback** maintenance tool
  (e.g. from a remote console with no employee at the dashboard); it still
  works exactly as before.
- ``infrastructure.portal.session_connector.SessionConnector`` -- the
  normal day-to-day workflow: the "Se connecter"/"Reconnecter" button on
  the dashboard, which runs this as a background task of the running
  application instead of a second console process.

Both hold the same cross-process profile lock as the poller, so a
scheduled poll (or the other caller) never races a manual login.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

from rma_portal.application.dto import BrowserProfileLockedError, BrowserTeardownError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.camoufox_reader import is_authenticated_view_present
from rma_portal.infrastructure.portal.profile_lock import (
    acquire_profile_lock,
    mark_profile_teardown_unconfirmed,
)
from rma_portal.infrastructure.portal.session_state import (
    capture_session_state,
    origin_of,
    save_session_state,
)
from rma_portal.observability import log_stage, new_operation_id, operation_context

logger = logging.getLogger(__name__)

OpenAndWait = Callable[..., Awaitable[None]]
OnTeardownUnconfirmed = Callable[[], None]

_TEARDOWN_TIMEOUT_SECONDS = 30.0


async def launch_visible_browser_and_wait(
    settings: Settings,
    *,
    teardown_timeout_seconds: float = _TEARDOWN_TIMEOUT_SECONDS,
    on_teardown_unconfirmed: OnTeardownUnconfirmed | None = None,
) -> None:
    """Open the visible persistent profile and block until it is closed.

    Does **not** acquire the profile lock itself -- callers do that, since
    what happens on a lock conflict differs (a printed message for the CLI
    fallback vs. a silent, loggable skip for the in-app connector).

    Browser close (teardown) is bounded independently of the employee's
    own wait for the window to close (which is deliberately unbounded --
    see ``_wait_until_closed``): the same reasoning as
    ``SyncWorkflows.verify_session`` applies here -- a stalled
    ``AsyncCamoufox`` close must not leave CONNECTING stuck forever.

    A caller must keep the profile lock it holds unreleased whenever
    cleanup cannot be confirmed -- not just when this function raises
    :class:`BrowserTeardownError`, which it can only safely do when
    nothing else is already propagating from the body above (most
    commonly a cancellation from ``SessionConnector.shutdown()``, which
    must win instead of being replaced -- see the module docstring on
    cancellation; a navigation failure before the window ever closes is
    the same situation). ``on_teardown_unconfirmed``, if given, is called
    synchronously whenever cleanup cannot be confirmed regardless of any
    of that -- a caller passes something like ``lambda:
    mark_profile_teardown_unconfirmed(lock_path, lock, reason)`` bound to
    the lock it holds, so the profile lock protection applies in every
    case, not only the one where raising is also safe.
    """
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    connection_started = time.perf_counter()
    with log_stage(logger, "browser_startup"):
        manager = AsyncCamoufox(
            persistent_context=True,
            user_data_dir=str(settings.browser_profile_dir),
            headless=False,
            os="windows",
            geoip=False,
            exclude_addons=[DefaultAddons.UBO],
            locale=settings.portal_locale,
            timezone_id=settings.portal_timezone,
            firefox_user_prefs={"network.cookie.cookieBehavior": 4},
        )
        context = await manager.__aenter__()
    body_succeeded = False
    employee_wait_seconds = 0.0
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(settings.omegaflow_start_route, wait_until="domcontentloaded")
        wait_started = time.perf_counter()
        logged_in = await _wait_for_login_or_close(context, page)
        employee_wait_seconds = time.perf_counter() - wait_started
        if logged_in:
            # Captured -- and saved -- while the browser is still open,
            # *before* anything closes it: the persistent Firefox profile
            # alone does not carry the authenticated session forward past
            # this process exiting (see session_state.py). An early
            # manual close (the employee gave up, or closed it before this
            # was ever detected) skips this entirely and reports no
            # success -- whatever was saved from a previous successful
            # login is left exactly as it was.
            await _capture_and_save_session_state(context, page, settings)
        body_succeeded = True
    finally:
        try:
            with log_stage(logger, "browser_cleanup", timeout_s=teardown_timeout_seconds):
                await asyncio.wait_for(
                    manager.__aexit__(None, None, None), timeout=teardown_timeout_seconds
                )
        except Exception:  # noqa: BLE001 - reported via log_stage above, not swallowed
            if on_teardown_unconfirmed is not None:
                on_teardown_unconfirmed()
            if body_succeeded:
                # Nothing else is propagating from the try body -- safe to
                # raise this as the method's own outcome. If something
                # *is* already propagating (most commonly a cancellation
                # from SessionConnector.shutdown()), that exception must
                # win instead -- see the docstring above.
                raise BrowserTeardownError(
                    "Le nettoyage du navigateur de connexion n'a pas pu être confirmé "
                    f"après {teardown_timeout_seconds:.0f}s."
                ) from None
        # Separates time spent waiting on the employee to click through the
        # login (unbounded, not application processing) from everything
        # else this function did (startup, capture, cleanup).
        total_seconds = time.perf_counter() - connection_started
        processing_seconds = max(total_seconds - employee_wait_seconds, 0.0)
        logger.info(
            "connection attempt summary employee_wait_ms=%.1f processing_ms=%.1f",
            employee_wait_seconds * 1000,
            processing_seconds * 1000,
        )


_LOGIN_POLL_INTERVAL_SECONDS = 0.5


async def _wait_for_login_or_close(context, page) -> bool:
    """Races two outcomes while the visible login browser is open:
    positive OmegaFlow authentication (the same ``#view_1874`` check
    ``CamoufoxPortalReader.verify_authenticated`` relies on -- one
    definition of "authenticated", not a second one) vs. the employee
    closing the window first.

    Returns True only once authentication is positively detected -- the
    caller must then capture session state before the browser closes,
    which happens right afterward through the application's own bounded
    teardown, not a further wait for the employee to close it themselves.
    An early close (before that) returns False: no capture happens, and
    whatever was previously saved is left untouched.
    """

    async def _poll_for_login() -> None:
        while True:
            with contextlib.suppress(Exception):  # page mid-navigation, or closing -- retry
                if await is_authenticated_view_present(page):
                    return
            await asyncio.sleep(_LOGIN_POLL_INTERVAL_SECONDS)

    login_task = asyncio.create_task(_poll_for_login())
    close_task = asyncio.create_task(_wait_until_closed(context))
    try:
        done, _pending = await asyncio.wait(
            {login_task, close_task}, return_when=asyncio.FIRST_COMPLETED
        )
        return login_task in done
    finally:
        for task in (login_task, close_task):
            if not task.done():
                task.cancel()
        for task in (login_task, close_task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _capture_and_save_session_state(context, page, settings: Settings) -> None:
    """Best-effort: a capture/save failure here must never turn a
    successful, positively-detected login into a reported failure (the
    browser still closes normally either way) -- it only means the next
    connect attempt starts from whatever was saved before, same as if
    this login had never positively completed at all."""
    try:
        with log_stage(logger, "session_capture"):
            origin = origin_of(settings.omegaflow_base_url)
            state = await capture_session_state(context, page, origin=origin)
            save_session_state(settings.session_state_path, state)
    except Exception:  # noqa: BLE001 - logged by log_stage, never fails the login/close flow
        return
    local_storage_count = sum(len(o.get("localStorage", [])) for o in state.get("origins", []))
    session_storage_count = len(state.get("session_storage", {}).get("items", {}))
    logger.info(
        "état de session OmegaFlow capturé et enregistré "
        "cookies=%d local_storage=%d session_storage=%d",
        len(state.get("cookies", [])),
        local_storage_count,
        session_storage_count,
    )


async def run_session_setup(
    settings: Settings, *, open_and_wait: OpenAndWait = launch_visible_browser_and_wait
) -> None:
    """Configurer_Session_RMA.bat's blocking flow (fallback maintenance tool).

    Prefer the "Se connecter"/"Reconnecter" button on the dashboard for the
    normal workflow; this remains available when no employee is at the
    dashboard (e.g. initial setup, or a remote console session).
    """
    settings.ensure_directories()
    print("Ouverture du navigateur OmegaFlow...")
    print(
        "Connectez-vous manuellement avec le compte OmegaFlow partagé de "
        "l'agence, puis validez la session si demandé."
    )
    print(
        "Le mot de passe OmegaFlow n'est jamais demandé ni enregistré par "
        "cette application."
    )
    print("Fermez la fenêtre du navigateur une fois la connexion terminée.")

    with operation_context(new_operation_id("conn")):
        try:
            with acquire_profile_lock(settings.browser_lock_path) as lock:
                try:
                    await open_and_wait(
                        settings,
                        on_teardown_unconfirmed=lambda: mark_profile_teardown_unconfirmed(
                            settings.browser_lock_path, lock, "configuration manuelle de la session"
                        ),
                    )
                except BrowserTeardownError:
                    print(
                        "Le navigateur s'est fermé, mais son nettoyage n'a pas pu être "
                        "confirmé. Le profil restera indisponible tant que cet outil ou le "
                        "Portail RMA n'auront pas été redémarrés."
                    )
                    return
        except BrowserProfileLockedError:
            print(
                "Impossible de configurer la session : le profil du navigateur "
                "OmegaFlow est déjà utilisé (synchronisation en cours, ou une "
                "fenêtre de connexion est déjà ouverte depuis le tableau de "
                "bord). Réessayez dans quelques minutes."
            )
            return

    print("Session enregistrée. Vous pouvez démarrer le portail.")


_CLOSE_POLL_INTERVAL_SECONDS = 1.0


def _is_context_closed(context) -> bool:
    """Best-effort, version-tolerant check for an already-closed context.

    Playwright's Python ``BrowserContext`` does not expose a public
    ``is_closed()``; ``context.pages`` raises once the underlying connection
    is gone, which is the same signal a closed context gives us either way.
    """
    try:
        return not context.pages
    except Exception:  # noqa: BLE001 - any access failure means "gone"
        return True


async def _wait_until_closed(context) -> None:
    """Block until ``context`` closes, tolerating three real-world cases the
    single ``context.on("close", ...)`` listener missed:

    1. The context is *already* closed by the time this is called (the
       employee closed the window fast, or the browser process died) --
       ``closed`` would then never receive its "close" event and this would
       hang forever.
    2. The event fires on the last *page* closing rather than (or before)
       the context itself, depending on how the OS/window manager tore the
       browser process down -- but with more than one page open (e.g. an
       extra tab), closing just one must not end the wait; only the *last*
       page closing (or the context itself closing) may.
    3. Camoufox/Playwright's "close" event never fires at all (observed
       live) -- a bounded poll of ``context.pages`` is the fallback so this
       can never hang indefinitely on event-emission quirks.
    """
    if _is_context_closed(context):
        return

    loop = asyncio.get_running_loop()
    closed = loop.create_future()

    def _mark_closed(*_args: object) -> None:
        if not closed.done():
            closed.set_result(None)

    def _on_page_closed(*_args: object) -> None:
        # A single page closing (out of possibly several open at once --
        # e.g. an extra tab the employee opened) must not end the wait: only
        # finish once no pages remain, or the context itself closes.
        if _is_context_closed(context):
            _mark_closed()

    def _watch_page(page: object) -> None:
        with contextlib.suppress(Exception):
            page.on("close", _on_page_closed)

    with contextlib.suppress(Exception):
        context.on("close", _mark_closed)
    with contextlib.suppress(Exception):
        context.on("page", lambda page: _watch_page(page))
    for page in list(getattr(context, "pages", ())):
        _watch_page(page)

    while not closed.done():
        if _is_context_closed(context):
            _mark_closed()
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(closed), timeout=_CLOSE_POLL_INTERVAL_SECONDS)
