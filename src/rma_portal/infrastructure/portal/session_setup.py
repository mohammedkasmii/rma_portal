"""Manual OmegaFlow session configuration (Configurer_Session_RMA.bat).

This opens a *visible* persistent Camoufox profile so an administrator can
log in and pass any session-validation step by hand. It never reads, asks
for, or stores the OmegaFlow password -- see docs/architecture.md section
6. It holds the same cross-process profile lock as the poller, so a
scheduled poll never races a manual login.
"""

from __future__ import annotations

import asyncio

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

from rma_portal.application.dto import BrowserProfileLockedError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock


async def run_session_setup(settings: Settings) -> None:
    settings.ensure_directories()
    try:
        with acquire_profile_lock(settings.browser_lock_path):
            await _open_visible_session(settings)
    except BrowserProfileLockedError:
        print(
            "Impossible de configurer la session : le portail utilise déjà le "
            "profil du navigateur (synchronisation en cours). Réessayez dans "
            "quelques minutes."
        )


async def _open_visible_session(settings: Settings) -> None:
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
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
