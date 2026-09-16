"""Read-only Camoufox/Playwright adapter implementing ``PortalReader``.

Everything this module does to the OmegaFlow page is navigation, selection
and reading -- see ``_read_only_route`` for the network-level enforcement
and docs/architecture.md section 7 for the full read-only policy. Some
steps (notably revealing the filter panel before ``#kn-submit-filters``
appears) are best-effort against a captured DOM snapshot and are flagged in
the project report as requiring a live OmegaFlow session to confirm.
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

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
from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock

logger = logging.getLogger(__name__)

_WRITE_HOST_MARKERS = ("omegaflow.ma", "knack.com")
_FILTER_TOGGLE_PATTERN = re.compile("ajouter des filtres", re.IGNORECASE)


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
        locale: str = "fr-FR",
        headless: bool = True,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
        self._procedure_value = procedure_value
        self._timezone_id = timezone_id
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
        locale: str,
        headless: bool,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
        self._procedure_value = procedure_value
        self._timezone_id = timezone_id
        self._locale = locale
        self._headless = headless
        self._lock_cm = None
        self._manager: AsyncCamoufox | None = None
        self._context = None
        self._page = None
        self.blocked_write_attempts: list[str] = []

    async def __aenter__(self) -> CamoufoxPortalReader:
        self._lock_cm = acquire_profile_lock(self._lock_path)
        self._lock_cm.__enter__()
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
        try:
            if self._manager is not None:
                await self._manager.__aexit__(exc_type, exc, traceback)
        finally:
            self._context = None
            self._page = None
            if self._lock_cm is not None:
                self._lock_cm.__exit__(exc_type, exc, traceback)
                self._lock_cm = None

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

    async def _reveal_filter_submit_button(self, page: Any) -> None:
        """Best-effort: expand the filter panel if the submit button is hidden.

        RMA_FIRST only captured ``#kn-submit-filters`` after this panel was
        already expanded; the exact toggle selector was not captured. The
        fallback text search is scoped to ``#view_1874`` so it can never
        click an unrelated "ajouter des filtres" link elsewhere on the page.
        This is a no-op if the button is already visible. Confirm against a
        live session.
        """
        view = page.locator("#view_1874")
        submit = view.locator("#kn-submit-filters")
        if await submit.count() > 0 and await submit.first.is_visible():
            return
        toggle = view.locator(".kn-add-filter")
        if await toggle.count() == 0:
            toggle = view.get_by_text(_FILTER_TOGGLE_PATTERN)
        if await toggle.count() > 0:
            await toggle.first.click()
            await submit.first.wait_for(state="visible", timeout=10_000)

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
        await self._reveal_filter_submit_button(page)

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

        await view.locator("#kn-submit-filters").click()
        await self._wait_for_view_refresh(page)

    async def _wait_for_view_refresh(self, page: Any) -> None:
        """Wait for the AJAX-driven view refresh to settle.

        Deliberately does not require any row to exist: a successfully
        filtered view with zero dossiers is valid and must produce a
        COMPLETE snapshot with zero rows (see docs/omegaflow-contract.md).
        Knack views typically show a transient loading indicator while an
        AJAX refresh is in flight; if present, wait for it to clear before
        falling back to the usual network-idle settle. Confirm the loading
        indicator's exact markup against a live session.
        """
        loading = page.locator("#view_1874.kn-loading, #view_1874 .kn-loading-spinner")
        with contextlib.suppress(Exception):
            if await loading.count() > 0:
                await loading.first.wait_for(state="detached", timeout=20_000)
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
