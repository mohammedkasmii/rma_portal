"""Read-only Camoufox/Playwright adapter implementing ``PortalReader``.

Everything this module does to the OmegaFlow page is navigation, selection
and reading -- see ``_read_only_route`` for the network-level enforcement
and docs/architecture.md section 7 for the full read-only policy.
"""

from __future__ import annotations

import contextlib
import logging
import time
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
from rma_portal.observability import log_stage, strip_query

logger = logging.getLogger(__name__)

_WRITE_HOST_MARKERS = ("omegaflow.ma", "knack.com")
_VERIFY_POLL_INTERVAL_MS = 500
_QUEUE_VIEW_POLL_INTERVAL_MS = 500
_QUEUE_VIEW_TIMEOUT_SECONDS = 45.0

AUTHENTICATED_VIEW_SELECTOR = "#view_1874"
# Captured selector for the Garage agréé search form's submit button --
# see _submit_search. Knack toggles an "is-loading" class on this exact
# element while the AJAX-driven search is in flight.
_SEARCH_SUBMIT_SELECTOR = '#view_1874 form.kn-search_form button[type="submit"]'

# Captured pagination markup: two identical ".kn-page-select" dropdowns
# (top/bottom of the list) and a ".kn-change-page.kn-next" control that
# gains an additional "disabled" class only once there is truly no next
# page -- see _go_to_page and _verify_last_page_reached.
_PAGE_SELECT_SELECTOR = "#view_1874 .kn-page-select"
_NEXT_PAGE_SELECTOR = "#view_1874 .kn-change-page.kn-next"
_PAGE_TRANSITION_TIMEOUT_MS = 20_000
_ROW_IDS_JS = (
    "() => Array.from(document.querySelectorAll("
    "'#view_1874 table tbody tr[id]')).map(r => r.id).sort().join(',')"
)
# Two conditions, both required: every ".kn-page-select" (there are two,
# top and bottom -- only the first is ever written to directly) shows the
# requested page, AND the rendered row set differs from what was on
# screen before navigating. Checking only the dropdown this code itself
# just set (the previous implementation) passes the instant Playwright
# writes that value -- before Knack's AJAX-driven refresh has even
# started, since the *second*, untouched dropdown only updates once
# Knack's own view re-render actually happens.
_PAGE_TRANSITION_JS = """([selector, expected, beforeIds]) => {
    const selects = document.querySelectorAll(selector);
    if (selects.length === 0) return false;
    for (const s of selects) { if (s.value !== expected) return false; }
    const rows = Array.from(document.querySelectorAll(
        '#view_1874 table tbody tr[id]'
    )).map(r => r.id).sort().join(',');
    return rows !== beforeIds;
}"""


class _StalePageResultError(RuntimeError):
    """A pagination page's completion wait passed, but its rows exactly
    match the previous page's -- OmegaFlow pages never legitimately repeat
    the same dossier set, so this is almost certainly a stale render
    rather than genuinely identical data. Deliberately a plain
    ``RuntimeError`` (not ``PortalReadError``): raised only after at least
    one page has already been collected, so ``read_agreement_queue``'s
    generic exception handler wraps it as ``PortalPartialReadError`` with
    whatever pages were already gathered, instead of discarding them as a
    hard failure.
    """

    def __init__(self, page_number: int) -> None:
        super().__init__(
            f"La page {page_number} affiche les mêmes dossiers que la page précédente "
            "(résultat probablement obsolète)."
        )


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

    def has_saved_session(self) -> bool:
        return self._session_state_path.exists()


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
            with log_stage(logger, "browser_startup"):
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
            with log_stage(logger, "session_restore"):
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
                with log_stage(logger, "browser_cleanup"):
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
            logger.warning(
                "blocked non-read-only OmegaFlow request: %s %s", method, strip_query(request.url)
            )
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

        with log_stage(logger, "garage_agree_selection"):
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
        with log_stage(logger, "search_submission", selector=_SEARCH_SUBMIT_SELECTOR):
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
        """Navigates to ``page_number`` and waits for genuine evidence the
        new page's results have actually rendered -- see
        ``_PAGE_TRANSITION_JS`` for the two conditions this requires
        together. Raises Playwright's own ``TimeoutError`` (a plain
        ``Exception``, not ``PortalReadError``) if neither materializes
        within :data:`_PAGE_TRANSITION_TIMEOUT_MS` -- caught by
        ``read_agreement_queue``'s generic handler, which preserves
        whatever pages were already collected as a ``PARTIAL`` result
        rather than discarding them.
        """
        select = page.locator(_PAGE_SELECT_SELECTOR).first
        before_row_ids = await page.evaluate(_ROW_IDS_JS)
        await select.select_option(str(page_number))
        await page.wait_for_function(
            _PAGE_TRANSITION_JS,
            arg=[_PAGE_SELECT_SELECTOR, str(page_number), before_row_ids],
            timeout=_PAGE_TRANSITION_TIMEOUT_MS,
        )
        await self._settle(page)

    async def _verify_last_page_reached(self, page: Any, total_pages: int) -> None:
        """Cross-checks the captured 'Next' control evidence against
        ``total_pages`` after the last page has been visited --
        ``.kn-change-page.kn-next`` only gains its additional ``disabled``
        class once there truly is no next page. Guards against silently
        under-reporting the queue if the page count read from page 1 ever
        lagged the real data (e.g. a dossier appeared between that read
        and this check). A missing control (no pagination widget at all)
        is not an error -- a genuinely single-page result may not render
        one.
        """
        next_control = page.locator(_NEXT_PAGE_SELECTOR).first
        if await next_control.count() == 0:
            return
        classes = (await next_control.get_attribute("class")) or ""
        if "disabled" not in classes.split():
            raise RuntimeError(
                f"La file OmegaFlow semble contenir plus de {total_pages} page(s) que prévu "
                "(le bouton 'suivant' n'est pas désactivé après la dernière page lue)."
            )

    async def _wait_for_queue_view(self, page: Any) -> None:
        """Polls for the authenticated queue view instead of a single blind
        ``wait_for()`` -- right after navigation, Knack's page is often
        still just an unauthenticated loading shell (no login marker yet,
        same as ``verify_authenticated``'s own reasoning), so a one-time
        auth check immediately after ``goto`` is not enough: the login/
        session-validation screen can render moments later, well before
        the :data:`_QUEUE_VIEW_TIMEOUT_SECONDS` a queue view that will
        never appear would otherwise burn. Reuses
        ``_assert_authenticated``/``is_authenticated_view_present``, the
        same definitions used everywhere else, so an unauthenticated
        session is caught within one poll interval instead of the full
        timeout.
        """
        deadline = time.monotonic() + _QUEUE_VIEW_TIMEOUT_SECONDS
        while True:
            await self._assert_authenticated()
            if await is_authenticated_view_present(page):
                return
            if time.monotonic() >= deadline:
                raise PortalReadError(
                    "Délai dépassé en attendant la file OmegaFlow "
                    f"(étape: affichage de la file, après {_QUEUE_VIEW_TIMEOUT_SECONDS:.0f}s)."
                )
            await page.wait_for_timeout(_QUEUE_VIEW_POLL_INTERVAL_MS)

    async def read_agreement_queue(self) -> QueueSnapshot:
        page = self._require_page()
        pages_collected: list[QueueSnapshot] = []
        try:
            with log_stage(logger, "queue_navigation"):
                await page.goto(self._start_route, wait_until="domcontentloaded", timeout=60_000)
                await self._wait_for_queue_view(page)
            await self._apply_garage_agree_filter(page)

            html = await page.content()
            await self._assert_authenticated()
            first_page = parse_queue_page(html)
            pages_collected.append(first_page)
            # Read from the same fully-settled HTML the rows themselves
            # came from -- _apply_garage_agree_filter's own completion
            # wait (the search button's "is-loading" class clearing) is
            # the evidence this is the fully rendered filtered result,
            # not a partial/loading render.
            total_pages = parse_page_count(html)
            logger.info(
                "stage=pagination outcome=PAGE_COLLECTED page=1 total_pages=%d rows=%d",
                total_pages,
                len(first_page.rows),
            )

            for page_number in range(2, total_pages + 1):
                with log_stage(logger, "pagination_page", page=page_number, total=total_pages):
                    await self._go_to_page(page, page_number)
                    html = await page.content()
                    await self._assert_authenticated()
                    page_snapshot = parse_queue_page(html)
                    # Belt-and-suspenders on top of _go_to_page's own
                    # row-change wait: never accept a page whose rows
                    # exactly match the one before it, even if the wait
                    # condition technically passed (e.g. a race between
                    # the two dropdowns re-rendering and the row table).
                    previous_ids = {row.record_id for row in pages_collected[-1].rows}
                    current_ids = {row.record_id for row in page_snapshot.rows}
                    if current_ids and current_ids == previous_ids:
                        raise _StalePageResultError(page_number)
                    pages_collected.append(page_snapshot)
                    logger.info(
                        "stage=pagination outcome=PAGE_COLLECTED page=%d total_pages=%d rows=%d",
                        page_number,
                        total_pages,
                        len(page_snapshot.rows),
                    )

            with log_stage(logger, "pagination_verify_last_page", total_pages=total_pages):
                await self._verify_last_page_reached(page, total_pages)
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

        merged = merge_snapshots(pages_collected)
        logger.info(
            "stage=pagination outcome=OK total_pages=%d unique_rows=%d",
            total_pages,
            len(merged.rows),
        )
        return merged

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
        with log_stage(logger, "auth_verification"):
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
