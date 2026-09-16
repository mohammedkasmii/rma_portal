"""Read-only Camoufox/Playwright adapter implementing ``PortalReader``.

Everything this module does to the OmegaFlow page is navigation, selection
and reading -- see ``_read_only_route`` for the network-level enforcement
and docs/architecture.md section 7 for the full read-only policy.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox
from filelock import FileLock

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    DetailReadError,
    DossierDetails,
    PortalAuthRequiredError,
    PortalDossierRef,
    PortalPartialReadError,
    PortalReadError,
    QueueSnapshot,
)
from rma_portal.infrastructure.portal.parser import (
    detect_auth_required,
    merge_snapshots,
    parse_dossier_details,
    parse_page_count,
    parse_queue_page,
)
from rma_portal.infrastructure.portal.profile_lock import (
    acquire_profile_lock,
    mark_profile_teardown_unconfirmed,
)
from rma_portal.infrastructure.portal.session_state import (
    load_session_state,
    origin_of,
    restore_session_state,
)

logger = logging.getLogger(__name__)

_WRITE_HOST_MARKERS = ("omegaflow.ma", "knack.com")
_VERIFY_POLL_INTERVAL_MS = 500

AUTHENTICATED_VIEW_SELECTOR = "#view_1874"
# Captured selector for the Garage agréé search form's submit button --
# see _submit_search. Knack toggles an "is-loading" class on this exact
# element while the AJAX-driven search is in flight.
_SEARCH_SUBMIT_SELECTOR = '#view_1874 form.kn-search_form button[type="submit"]'


async def is_authenticated_view_present(page: Any) -> bool:
    """True once the authenticated queue view has genuinely rendered --
    not just the absence of a login screen, which an unauthenticated
    loading shell also satisfies. The one definition of "authenticated"
    shared by ``verify_authenticated`` below and the login-capture polling
    in ``session_setup.launch_visible_browser_and_wait``."""
    view = page.locator(AUTHENTICATED_VIEW_SELECTOR)
    return await view.count() > 0 and await view.first.is_visible()


class CamoufoxPortalReaderFactory:
    """The V1 ``PortalReaderFactory``. See ``application.ports`` for the contract."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        lock_path: Path,
        start_route: str,
        base_url: str,
        procedure_value: str,
        timezone_id: str,
        session_state_path: Path,
        locale: str = "fr-FR",
        headless: bool = True,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
        self._procedure_value = procedure_value
        self._timezone_id = timezone_id
        self._session_state_path = session_state_path
        self._locale = locale
        self._headless = headless

    def open(self) -> CamoufoxPortalReader:
        return CamoufoxPortalReader(
            profile_dir=self._profile_dir,
            lock_path=self._lock_path,
            start_route=self._start_route,
            base_url=self._base_url,
            procedure_value=self._procedure_value,
            timezone_id=self._timezone_id,
            session_state_path=self._session_state_path,
            locale=self._locale,
            headless=self._headless,
        )


class CamoufoxPortalReader:
    def __init__(
        self,
        *,
        profile_dir: Path,
        lock_path: Path,
        start_route: str,
        base_url: str,
        procedure_value: str,
        timezone_id: str,
        session_state_path: Path,
        locale: str,
        headless: bool,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
        self._procedure_value = procedure_value
        self._timezone_id = timezone_id
        self._session_state_path = session_state_path
        self._locale = locale
        self._headless = headless
        self._lock_cm = None
        self._lock: FileLock | None = None
        self._manager: AsyncCamoufox | None = None
        self._context = None
        self._page = None
        self.blocked_write_attempts: list[str] = []

    async def __aenter__(self) -> CamoufoxPortalReader:
        self._lock_cm = acquire_profile_lock(self._lock_path)
        self._lock = self._lock_cm.__enter__()
        try:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            self._manager = AsyncCamoufox(
                persistent_context=True,
                user_data_dir=str(self._profile_dir),
                headless=self._headless,
                humanize=True,
                os="windows",
                geoip=False,
                exclude_addons=[DefaultAddons.UBO],
                locale=self._locale,
                timezone_id=self._timezone_id,
                firefox_user_prefs={"network.cookie.cookieBehavior": 4},
            )
            self._context = await self._manager.__aenter__()
            await self._context.route("**/*", self._read_only_route)
            # Explicit restore before any navigation happens: the
            # persistent Firefox profile alone does not carry sessionStorage
            # forward across a process restart (by spec), and OmegaFlow's
            # own authenticated render depends on it -- see session_state.py.
            saved_state = load_session_state(self._session_state_path)
            await restore_session_state(
                self._context, saved_state, origin=origin_of(self._base_url)
            )
            self._page = (
                self._context.pages[0] if self._context.pages else await self._context.new_page()
            )
        except BaseException:
            self._lock_cm.__exit__(None, None, None)
            self._lock_cm = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        manager_closed = False
        try:
            if self._manager is not None:
                await self._manager.__aexit__(exc_type, exc, traceback)
            manager_closed = True
        finally:
            self._context = None
            self._page = None
            if self._lock_cm is not None:
                if not manager_closed and self._lock is not None:
                    # Teardown did not complete (e.g. a caller's bounding
                    # asyncio.wait_for gave up on a stalled close) -- never
                    # release the lock in that case, or a fresh launch could
                    # race a browser process that might still be running
                    # against this same profile.
                    mark_profile_teardown_unconfirmed(
                        self._lock_path, self._lock, "nettoyage du navigateur OmegaFlow"
                    )
                self._lock_cm.__exit__(exc_type, exc, traceback)
                self._lock_cm = None
                self._lock = None

    async def _read_only_route(self, route: Any, request: Any) -> None:
        method = request.method.upper()
        url = request.url.lower()
        is_portal_host = any(marker in url for marker in _WRITE_HOST_MARKERS)
        blocked = is_portal_host and (
            method in {"PUT", "PATCH", "DELETE"} or (method == "POST" and "/records" in url)
        )
        if blocked:
            self.blocked_write_attempts.append(f"{method} {request.url}")
            logger.warning("blocked non-read-only OmegaFlow request: %s %s", method, url)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("CamoufoxPortalReader must be used as an async context manager")
        return self._page

    @staticmethod
    async def _settle(page: Any) -> None:
        with contextlib.suppress(Exception):  # best-effort settle, never fatal
            await page.wait_for_load_state("networkidle", timeout=5_000)
        await page.wait_for_timeout(250)

    async def _assert_authenticated(self) -> None:
        page = self._require_page()
        html = await page.content()
        if detect_auth_required(html):
            raise PortalAuthRequiredError(
                "La session OmegaFlow doit être reconnectée (Configurer_Session_RMA.bat)."
            )

    async def _apply_garage_agree_filter(self, page: Any) -> None:
        """Select 'Garage agréé' on the Chosen-hidden native ``<select>``.

        The real element is rendered ``style="display: none"`` behind
        OmegaFlow's Chosen widget (RMA_FIRST), so a plain
        ``select_option()`` would perform Playwright's visibility
        actionability check and hang. ``force=True`` bypasses that check;
        the native value is then read back to confirm the selection stuck,
        and ``input``/``change`` are dispatched explicitly in case anything
        downstream (Knack's own filtering, not the cosmetic Chosen UI)
        depends on them rather than on Playwright's own event dispatch.
        """
        view = page.locator("#view_1874")

        procedure = view.locator("#kn-conn-1-field_219")
        await procedure.wait_for(state="attached", timeout=15_000)
        await procedure.select_option(value=self._procedure_value, force=True, timeout=15_000)

        actual_value = await procedure.input_value()
        if actual_value != self._procedure_value:
            raise PortalReadError(
                "Le filtre Garage agréé n'a pas pu être appliqué "
                f"(valeur obtenue: {actual_value!r})."
            )
        await procedure.evaluate(
            "el => {"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true}));"
            "}"
        )

        await self._submit_search(page)

    async def _submit_search(self, page: Any) -> None:
        """Clicks the search form's submit button -- captured selector:
        ``#view_1874 form.kn-search_form button[type="submit"]`` -- and
        waits for the AJAX-driven search to complete.

        Completion is read from the button's own ``is-loading`` class,
        which Knack toggles on this exact element while the request is in
        flight: first a short, best-effort wait for the class to *appear*
        (the request may already be done by the time this checks, in
        which case there is nothing to observe starting), then a required
        wait for it to *disappear*. Deliberately does not require any row
        to exist afterward: a successfully filtered view with zero
        dossiers is valid and must produce a COMPLETE snapshot with zero
        rows (see docs/omegaflow-contract.md).
        """
        button = page.locator(_SEARCH_SUBMIT_SELECTOR)
        try:
            await button.wait_for(state="visible", timeout=15_000)
        except Exception as exc:
            raise PortalReadError(
                "Bouton de recherche OmegaFlow introuvable (étape: affichage du bouton, "
                f"sélecteur: {_SEARCH_SUBMIT_SELECTOR!r})."
            ) from exc

        await button.click()

        loading_button = page.locator(f"{_SEARCH_SUBMIT_SELECTOR}.is-loading")
        # Best-effort: the AJAX request may already be done by the time this
        # checks, in which case there is nothing to observe starting.
        with contextlib.suppress(Exception):
            await loading_button.wait_for(state="attached", timeout=2_000)
        try:
            await loading_button.wait_for(state="detached", timeout=20_000)
        except Exception as exc:
            raise PortalReadError(
                "Délai dépassé en attendant la fin de la recherche OmegaFlow "
                f"(étape: recherche, sélecteur: {_SEARCH_SUBMIT_SELECTOR!r})."
            ) from exc

        await self._settle(page)

    async def _go_to_page(self, page: Any, page_number: int) -> None:
        select = page.locator("#view_1874 .kn-page-select").first
        await select.select_option(str(page_number))
        await page.wait_for_function(
            "([selector, expected]) => document.querySelector(selector)?.value === expected",
            arg=["#view_1874 .kn-page-select", str(page_number)],
            timeout=20_000,
        )
        await self._settle(page)

    async def read_agreement_queue(self) -> QueueSnapshot:
        page = self._require_page()
        pages_collected: list[QueueSnapshot] = []
        try:
            await page.goto(self._start_route, wait_until="domcontentloaded", timeout=60_000)
            await self._assert_authenticated()
            await page.locator("#view_1874").wait_for(state="visible", timeout=45_000)
            await self._apply_garage_agree_filter(page)

            html = await page.content()
            await self._assert_authenticated()
            pages_collected.append(parse_queue_page(html))
            total_pages = parse_page_count(html)

            for page_number in range(2, total_pages + 1):
                await self._go_to_page(page, page_number)
                html = await page.content()
                await self._assert_authenticated()
                pages_collected.append(parse_queue_page(html))
        except PortalAuthRequiredError:
            raise
        except PortalReadError:
            # Already a well-typed, well-messaged domain error (e.g. the
            # Garage agréé filter failing to apply) -- propagate as-is
            # instead of losing its message inside a generic wrapper.
            raise
        except Exception as exc:
            message = f"Lecture incomplète de la liste OmegaFlow ({type(exc).__name__})."
            if not pages_collected:
                raise PortalReadError(message) from exc
            raise PortalPartialReadError(message, partial=merge_snapshots(pages_collected)) from exc

        return merge_snapshots(pages_collected)

    async def verify_authenticated(self) -> None:
        """Short, read-only check: navigate to the queue's start route and
        require *positive* evidence the authenticated app actually
        rendered -- the same ``#view_1874`` container ``read_agreement_queue``
        waits for, before any filter/pagination/detail work.

        The absence of a login/session-validation screen is not enough on
        its own: right after navigation, Knack's page is often still just
        an unauthenticated loading shell (e.g. ``<div id="knack-body">
        Loading...</div>``), which has neither a login marker nor the
        rendered view -- treating that as "authenticated" would be a false
        READY. This polls both conditions (reusing the exact same
        ``_assert_authenticated``/``detect_auth_required`` logic used
        everywhere else) until one becomes true; an unresolved loading
        shell therefore never resolves here and is left to the caller's
        own bound (``SyncAgreementQueue.verify_session``) to turn into a
        timeout/ERROR, never a false READY.
        """
        page = self._require_page()
        await page.goto(self._start_route, wait_until="domcontentloaded", timeout=30_000)
        while True:
            await self._assert_authenticated()
            if await is_authenticated_view_present(page):
                return
            await page.wait_for_timeout(_VERIFY_POLL_INTERVAL_MS)

    async def read_dossier_details(self, dossier: PortalDossierRef) -> DossierDetails:
        page = self._require_page()
        target = urljoin(self._base_url, dossier.details_href)
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            await self._assert_authenticated()
            await page.locator(".field_114 .kn-detail-body").wait_for(timeout=30_000)
            await self._settle(page)
            html = await page.content()
            await self._assert_authenticated()
        except PortalAuthRequiredError:
            raise
        except Exception as exc:
            raise DetailReadError(
                f"Lecture du détail impossible pour {dossier.record_id} ({type(exc).__name__})."
            ) from exc

        dates = parse_dossier_details(html, self._timezone_id)
        return DossierDetails(dates=dates, detail_complete=True, detail_error=None)


__all__ = [
    "BrowserProfileLockedError",
    "CamoufoxPortalReader",
    "CamoufoxPortalReaderFactory",
]
