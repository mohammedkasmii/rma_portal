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
from collections.abc import Awaitable, Callable

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock

OpenAndWait = Callable[[Settings], Awaitable[None]]


async def launch_visible_browser_and_wait(settings: Settings) -> None:
    """Open the visible persistent profile and block until it is closed.

    Does **not** acquire the profile lock itself -- callers do that, since
    what happens on a lock conflict differs (a printed message for the CLI
    fallback vs. a silent, loggable skip for the in-app connector).
    """
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(settings.browser_profile_dir),
        headless=False,
        os="windows",
        geoip=False,
        exclude_addons=[DefaultAddons.UBO],
        locale=settings.portal_locale,
        timezone_id=settings.portal_timezone,
        firefox_user_prefs={"network.cookie.cookieBehavior": 4},
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(settings.omegaflow_start_route, wait_until="domcontentloaded")
        await _wait_until_closed(context)


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

    try:
        with acquire_profile_lock(settings.browser_lock_path):
            await open_and_wait(settings)
    except BrowserProfileLockedError:
        print(
            "Impossible de configurer la session : le profil du navigateur "
            "OmegaFlow est déjà utilisé (synchronisation en cours, ou une "
            "fenêtre de connexion est déjà ouverte depuis le tableau de "
            "bord). Réessayez dans quelques minutes."
        )
        return

    print("Session enregistrée. Vous pouvez démarrer le portail.")


async def _wait_until_closed(context) -> None:
    closed = asyncio.get_running_loop().create_future()

    def _on_close(*_args: object) -> None:
        if not closed.done():
            closed.set_result(None)

    context.on("close", _on_close)
    try:
        await closed
    except Exception:  # noqa: BLE001 - fall back to polling if events misbehave
        while context.pages:
            await asyncio.sleep(1)
