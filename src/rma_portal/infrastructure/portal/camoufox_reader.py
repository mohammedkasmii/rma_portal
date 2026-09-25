"""Read-only Camoufox/Playwright adapter implementing ``PortalReader``.

Everything this module does to the OmegaFlow page is navigation, selection
and reading -- see ``_read_only_route`` for the network-level enforcement
and docs/architecture.md section 7 for the full read-only policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
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
    DossierDetailValues,
    PortalAuthRequiredError,
    PortalDossierRef,
    PortalReadError,
    WorkflowReadOutcome,
    WorkflowSnapshot,
)
from rma_portal.domain.enums import PollStatus
from rma_portal.domain.workflow_definition import FieldSpec, WorkflowDefinition
from rma_portal.infrastructure.portal.parser import detect_auth_required
from rma_portal.infrastructure.portal.profile_lock import (
    acquire_profile_lock,
    mark_profile_teardown_unconfirmed,
)
from rma_portal.infrastructure.portal.session_state import (
    load_session_state,
    origin_of,
    restore_session_state,
)
from rma_portal.infrastructure.portal.workflow_parser import (
    has_enabled_next,
    has_table_header,
    merge_workflow_snapshots,
    parse_detail_fields,
    parse_total_pages,
    parse_workflow_page,
)
from rma_portal.observability import log_stage, strip_query

logger = logging.getLogger(__name__)

_WRITE_HOST_MARKERS = ("omegaflow.ma", "knack.com")
_VERIFY_POLL_INTERVAL_MS = 500
_QUEUE_VIEW_POLL_INTERVAL_MS = 500
_QUEUE_VIEW_TIMEOUT_SECONDS = 45.0
# Once the root is visible its child controls (filter select, search button) render within
# moments; a root without them is a scene that was not rebuilt, so recovery starts much sooner
# than the full timeout, which is kept for a root that never appears.
_CHILD_CONTROL_TIMEOUT_SECONDS = 10.0

AUTHENTICATED_VIEW_SELECTOR = "#view_1874"
# Captured selector for the Garage agréé search form's submit button --
# see _submit_search. Knack toggles an "is-loading" class on this exact
# element while the AJAX-driven search is in flight.
_SEARCH_SUBMIT_SELECTOR = '#view_1874 form.kn-search_form button[type="submit"]'

# Captured pagination markup: two identical ".kn-page-select" dropdowns
# (top/bottom of the list) and a ".kn-change-page.kn-next" control that
# gains an additional "disabled" class only once there is truly no next
# page. Every selector below is scoped to one view root (``#view_N``), so
# two views on the same route (e.g. the two Carence tables) never mix.
_PAGE_SELECT = ".kn-page-select"
_NEXT_PAGE = ".kn-change-page.kn-next"
_LEGACY_ROOT = AUTHENTICATED_VIEW_SELECTOR
_PAGE_TRANSITION_TIMEOUT_MS = 20_000
_MAX_PAGES = 200
_STABLE_POLL_MS = 300
_STABLE_TIMEOUT_SECONDS = 8.0
_DETAIL_READY_SELECTOR = ".kn-detail .kn-detail-body"
_HOME_ROUTE = "#accueil/"


def _row_ids_js(root: str) -> str:
    return (
        "() => Array.from(document.querySelectorAll("
        f"'{root} table tbody tr[id]')).map(r => r.id).sort().join(',')"
    )


# Two conditions, both required: every ".kn-page-select" (there are two,
# top and bottom -- only the first is ever written to directly) shows the
# requested page, AND the rendered row set differs from what was on
# screen before navigating. Checking only the dropdown this code itself
# just set passes the instant Playwright writes that value -- before Knack's
# AJAX-driven refresh has even started, since the *second*, untouched
# dropdown only updates once Knack's own view re-render actually happens.
_PAGE_TRANSITION_JS = """([selector, expected, beforeIds, rowsSelector]) => {
    const selects = document.querySelectorAll(selector);
    if (selects.length === 0) return false;
    for (const s of selects) { if (s.value !== expected) return false; }
    const rows = Array.from(document.querySelectorAll(rowsSelector))
        .map(r => r.id).sort().join(',');
    return rows !== beforeIds;
}"""

# Used when the last known dropdown option was passed and the "next" control is
# still enabled (the queue grew while it was being read): only row identity can
# prove the new page rendered.
_ROWS_CHANGED_JS = """([rowsSelector, beforeIds]) => {
    const rows = Array.from(document.querySelectorAll(rowsSelector))
        .map(r => r.id).sort().join(',');
    return rows !== beforeIds;
}"""


_VIEW_IDS_JS = "() => Array.from(document.querySelectorAll('[id^=\"view_\"]')).map(e => e.id)"


def _search_submit_selector(root: str) -> str:
    """The view's search-form submit button, scoped to the workflow root (captured structure)."""
    return f'{root} form.kn-search_form button[type="submit"]'


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


async def is_authenticated_view_present(page: Any, selector: str = AUTHENTICATED_VIEW_SELECTOR) -> bool:
    """True once an authenticated queue view has genuinely rendered --
    not just the absence of a login screen, which an unauthenticated
    loading shell also satisfies. The one definition of "authenticated"
    shared by ``verify_authenticated`` below, every workflow read and the
    login-capture polling in ``session_setup.launch_visible_browser_and_wait``."""
    view = page.locator(selector)
    return await view.count() > 0 and await view.first.is_visible()


def workflow_url(base_url: str, route: str) -> str:
    """The exact captured hash route under the portal origin (no slash after ``#``)."""
    return f"{base_url.rstrip('/')}/{route}"


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:300]


class CamoufoxPortalReaderFactory:
    """The V1 ``PortalReaderFactory``. See ``application.ports`` for the contract."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        lock_path: Path,
        start_route: str,
        base_url: str,
        timezone_id: str,
        session_state_path: Path,
        locale: str = "fr-FR",
        headless: bool = True,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
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
        timezone_id: str,
        session_state_path: Path,
        locale: str,
        headless: bool,
    ) -> None:
        self._profile_dir = profile_dir
        self._lock_path = lock_path
        self._start_route = start_route
        self._base_url = base_url
        self._timezone_id = timezone_id
        self._session_state_path = session_state_path
        self._locale = locale
        self._headless = headless
        self._lock_cm = None
        self._lock: FileLock | None = None
        self._manager: AsyncCamoufox | None = None
        self._context = None
        self._page = None
        self._current_route: str | None = None
        self._detail_cache: dict[str, DossierDetailValues] = {}
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
            self._current_route = None
            self._detail_cache.clear()
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

    async def _apply_filter(
        self, page: Any, root: str, control_selector: str, value: str, label: str
    ) -> None:
        """Select a captured option on the Chosen-hidden native ``<select>``.

        The real element is rendered ``style="display: none"`` behind
        OmegaFlow's Chosen widget (RMA_FIRST), so a plain
        ``select_option()`` would perform Playwright's visibility
        actionability check and hang. ``force=True`` bypasses that check;
        the native value is then read back to confirm the selection stuck,
        and ``input``/``change`` are dispatched explicitly in case anything
        downstream (Knack's own filtering, not the cosmetic Chosen UI)
        depends on them rather than on Playwright's own event dispatch.
        """
        view = page.locator(root)

        with log_stage(logger, "filter_selection", filter=label):
            control = view.locator(control_selector)
            await control.wait_for(state="attached", timeout=15_000)
            await control.select_option(value=value, force=True, timeout=15_000)

            actual_value = await control.input_value()
            if actual_value != value:
                raise PortalReadError(
                    f"Le filtre {label} n'a pas pu être appliqué "
                    f"(valeur obtenue: {actual_value!r})."
                )
            await control.evaluate(
                "el => {"
                " el.dispatchEvent(new Event('input', {bubbles: true}));"
                " el.dispatchEvent(new Event('change', {bubbles: true}));"
                "}"
            )

        await self._submit_search(page, root)

    async def _submit_search(self, page: Any, root: str = _LEGACY_ROOT) -> None:
        """Clicks the view's search form submit button -- captured selector:
        ``#view_N form.kn-search_form button[type="submit"]`` -- and waits for the
        AJAX-driven search to complete.

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
        submit_selector = _search_submit_selector(root)
        with log_stage(logger, "search_submission", selector=submit_selector):
            button = page.locator(submit_selector)
            try:
                await button.wait_for(state="visible", timeout=15_000)
            except Exception as exc:
                raise PortalReadError(
                    "Bouton de recherche OmegaFlow introuvable (étape: affichage du bouton, "
                    f"sélecteur: {submit_selector!r})."
                ) from exc

            await button.click()

            loading_button = page.locator(f"{submit_selector}.is-loading")
            # Best-effort: the AJAX request may already be done by the time this
            # checks, in which case there is nothing to observe starting.
            with contextlib.suppress(Exception):
                await loading_button.wait_for(state="attached", timeout=2_000)
            try:
                await loading_button.wait_for(state="detached", timeout=20_000)
            except Exception as exc:
                raise PortalReadError(
                    "Délai dépassé en attendant la fin de la recherche OmegaFlow "
                    f"(étape: recherche, sélecteur: {submit_selector!r})."
                ) from exc

            await self._settle(page)

    async def _go_to_page(self, page: Any, page_number: int, root: str = _LEGACY_ROOT) -> None:
        """Navigates to ``page_number`` and waits for genuine evidence the
        new page's results have actually rendered -- see
        ``_PAGE_TRANSITION_JS`` for the two conditions this requires
        together. Raises Playwright's own ``TimeoutError`` (a plain
        ``Exception``, not ``PortalReadError``) if neither materializes
        within :data:`_PAGE_TRANSITION_TIMEOUT_MS` -- callers turn that into
        a ``PARTIAL`` result that keeps whatever pages were already
        collected rather than discarding them.
        """
        rows_selector = f"{root} table tbody tr[id]"
        select = page.locator(f"{root} {_PAGE_SELECT}").first
        before_row_ids = await page.evaluate(_row_ids_js(root))
        await select.select_option(str(page_number))
        await page.wait_for_function(
            _PAGE_TRANSITION_JS,
            arg=[f"{root} {_PAGE_SELECT}", str(page_number), before_row_ids, rows_selector],
            timeout=_PAGE_TRANSITION_TIMEOUT_MS,
        )
        await self._settle(page)

    async def _click_next_page(self, page: Any, root: str) -> None:
        rows_selector = f"{root} table tbody tr[id]"
        before_row_ids = await page.evaluate(_row_ids_js(root))
        await page.locator(f"{root} {_NEXT_PAGE}").first.click()
        await page.wait_for_function(
            _ROWS_CHANGED_JS,
            arg=[rows_selector, before_row_ids],
            timeout=_PAGE_TRANSITION_TIMEOUT_MS,
        )
        await self._settle(page)

    async def _wait_for_stable_rows(self, page: Any, root: str) -> None:
        """Waits until the rendered row identity stops changing.

        Knack re-renders a view in steps (loading state, then rows); reading
        the DOM mid-render would record a partial page as if it were whole.
        Two identical row-id snapshots one poll apart count as settled. After
        the bound the page is used as-is: staleness across pages is still
        caught by the changed-row-identity checks of the pagination loop.
        """
        rows_js = _row_ids_js(root)
        deadline = time.monotonic() + _STABLE_TIMEOUT_SECONDS
        previous = await page.evaluate(rows_js)
        while time.monotonic() < deadline:
            await page.wait_for_timeout(_STABLE_POLL_MS)
            current = await page.evaluate(rows_js)
            if current == previous:
                return
            previous = current

    async def _wait_for_queue_view(
        self,
        page: Any,
        root: str = _LEGACY_ROOT,
        *,
        require_table_header: bool = False,
        require_selector: str | None = None,
    ) -> None:
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

        With ``require_table_header`` the table header must also be present:
        that is the positive evidence a queue with zero rows rendered, as
        opposed to a container that is still loading. ``require_selector`` (the
        filter control of a filtered workflow) must be present inside the root:
        a root that rendered without its controls is a scene that was not rebuilt.
        """
        deadline = time.monotonic() + _QUEUE_VIEW_TIMEOUT_SECONDS
        root_seen_at: float | None = None
        while True:
            await self._assert_authenticated()
            if root_seen_at is None and await is_authenticated_view_present(page, root):
                root_seen_at = time.monotonic()
            if (
                await is_authenticated_view_present(page, root)
                and (
                    not require_table_header
                    or await page.locator(f"{root} table thead th").count() > 0
                )
                and (require_selector is None or await page.locator(require_selector).count() > 0)
            ):
                return
            now = time.monotonic()
            if (
                require_selector is not None
                and root_seen_at is not None
                and now - root_seen_at >= _CHILD_CONTROL_TIMEOUT_SECONDS
            ):
                raise PortalReadError(
                    "Délai dépassé en attendant les contrôles de la file OmegaFlow "
                    f"(étape: contrôles de la file, après {_CHILD_CONTROL_TIMEOUT_SECONDS:.0f}s)."
                )
            if now >= deadline:
                raise PortalReadError(
                    "Délai dépassé en attendant la file OmegaFlow "
                    f"(étape: affichage de la file, après {_QUEUE_VIEW_TIMEOUT_SECONDS:.0f}s)."
                )
            await page.wait_for_timeout(_QUEUE_VIEW_POLL_INTERVAL_MS)

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
        own bound (``SyncWorkflows.verify_session``) to turn into a
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

    async def read_workflows(
        self, definitions: Sequence[WorkflowDefinition]
    ) -> dict[str, WorkflowReadOutcome]:
        """Reads every definition sequentially in this one authenticated context.

        Each workflow gets an independent outcome; one failure never stops or
        invalidates another.
        """
        outcomes: dict[str, WorkflowReadOutcome] = {}
        for definition in definitions:
            outcomes[definition.key] = await self.read_workflow(definition)
        return outcomes

    async def read_workflow(self, definition: WorkflowDefinition) -> WorkflowReadOutcome:
        """Reads one workflow's complete queue, never raising for a read problem.

        Returns COMPLETE, PARTIAL (rows collected before a mid-pagination
        failure), AUTH_REQUIRED or FAILED. Only cancellation propagates.
        """
        page = self._require_page()
        root = definition.root_selector
        collected: list[WorkflowSnapshot] = []
        writes_before = len(self.blocked_write_attempts)
        started = time.perf_counter()
        try:
            with log_stage(logger, "workflow_navigation", workflow=definition.key):
                await self._open_workflow_view(page, definition)
            if definition.filter is not None:
                spec = definition.filter
                await self._apply_filter(page, root, spec.control_selector, spec.value, spec.label)
                # A filtered view (e.g. the shared agreement view) only renders its table once the
                # filter is submitted: the header is the evidence, even for zero rows.
                await self._wait_for_queue_view(page, root, require_table_header=True)
            elif definition.submit_search_on_open:
                # Search-first view: submit its untouched search form; the table header (also
                # rendered, with zero rows, for an empty result) is then the evidence.
                await self._submit_search(page, root)
                await self._wait_for_queue_view(page, root, require_table_header=True)
            await self._wait_for_stable_rows(page, root)

            html = await page.content()
            await self._assert_authenticated()
            if not has_table_header(html, definition):
                raise PortalReadError(
                    f"La file « {definition.name} » n'affiche pas son tableau "
                    "(structure inattendue)."
                )
            collected.append(parse_workflow_page(html, definition))
            page_number = 1
            while True:
                total_pages = parse_total_pages(html, definition)
                logger.info(
                    "stage=pagination outcome=PAGE_COLLECTED workflow=%s page=%d total_pages=%d rows=%d",
                    definition.key,
                    page_number,
                    total_pages,
                    len(collected[-1].rows),
                )
                if page_number < total_pages:
                    use_select = True
                elif has_enabled_next(html, definition):
                    # The queue grew while it was being read: the dropdown does not list
                    # the new page yet, but the "next" control proves it exists.
                    use_select = False
                else:
                    break
                page_number += 1
                if page_number > _MAX_PAGES:
                    raise RuntimeError(f"Plus de {_MAX_PAGES} pages lues pour {definition.key}.")
                with log_stage(
                    logger, "pagination_page", workflow=definition.key, page=page_number
                ):
                    if use_select:
                        await self._go_to_page(page, page_number, root)
                    else:
                        await self._click_next_page(page, root)
                    await self._wait_for_stable_rows(page, root)
                    html = await page.content()
                    await self._assert_authenticated()
                    page_snapshot = parse_workflow_page(html, definition)
                    previous_ids = {row.record_id for row in collected[-1].rows}
                    current_ids = {row.record_id for row in page_snapshot.rows}
                    if current_ids and current_ids == previous_ids:
                        raise _StalePageResultError(page_number)
                    collected.append(page_snapshot)
        except PortalAuthRequiredError as exc:
            return self._outcome(definition, PollStatus.AUTH_REQUIRED, [], str(exc))
        except asyncio.CancelledError:
            raise
        except PortalReadError as exc:
            status = PollStatus.PARTIAL if collected else PollStatus.FAILED
            return self._outcome(definition, status, collected, str(exc))
        except Exception as exc:  # noqa: BLE001 - one workflow's failure is data, not a crash
            message = f"Lecture incomplète de « {definition.name} » ({_short_error(exc)})."
            status = PollStatus.PARTIAL if collected else PollStatus.FAILED
            return self._outcome(definition, status, collected, message)
        finally:
            if len(self.blocked_write_attempts) > writes_before:
                logger.error(
                    "workflow %s attempted %d blocked non-read-only request(s)",
                    definition.key,
                    len(self.blocked_write_attempts) - writes_before,
                )

        merged = merge_workflow_snapshots(definition, collected)
        logger.info(
            "stage=workflow_read outcome=COMPLETE workflow=%s pages=%d unique_rows=%d elapsed_ms=%.1f",
            definition.key,
            merged.pages_seen,
            len(merged.rows),
            (time.perf_counter() - started) * 1000,
        )
        return WorkflowReadOutcome(definition.key, PollStatus.COMPLETE, merged, None)

    @staticmethod
    def _outcome(
        definition: WorkflowDefinition,
        status: PollStatus,
        collected: Sequence[WorkflowSnapshot],
        error: str,
    ) -> WorkflowReadOutcome:
        snapshot = (
            merge_workflow_snapshots(definition, collected)
            if collected
            else WorkflowSnapshot(workflow_key=definition.key)
        )
        return WorkflowReadOutcome(definition.key, status, snapshot, error)

    async def _open_workflow_view(self, page: Any, definition: WorkflowDefinition) -> None:
        """Navigates to the workflow's captured route from a freshly rebuilt Knack scene.

        Every workflow starts by visiting the home route, whatever the previous state
        (about:blank, a queue on the same or another route, or a dossier detail page left
        by detail enrichment): a hash-only jump between two Knack scenes may keep the old
        scene, or leave a queue root without its filter controls, and pagination would not
        restart at page 1. If the expected view does not render, one bounded recovery goes
        through the home route again and reloads the target; a second failure is final.

        Readiness is two-phase for a filtered workflow: here the authenticated root *and*
        the filter control are required (the table only appears once the filter is
        submitted, see ``read_workflow``); an unfiltered workflow requires its table header.
        """
        spec = definition.filter
        if spec is not None:
            control: str | None = f"{definition.root_selector} {spec.control_selector}"
        elif definition.submit_search_on_open:
            control = _search_submit_selector(definition.root_selector)
        else:
            control = None
        for attempt in (1, 2):
            await self._goto_route_via_home(page, definition.route, reload=attempt == 2)
            try:
                await self._wait_for_queue_view(
                    page,
                    definition.root_selector,
                    require_table_header=control is None,
                    require_selector=control,
                )
                return
            except PortalReadError as exc:
                diagnostics = await self._queue_diagnostics(page, definition)
                if attempt == 2:
                    logger.error("stage=workflow_navigation outcome=FAILED %s", diagnostics)
                    raise PortalReadError(f"{exc} Diagnostic : {diagnostics}") from exc
                logger.warning(
                    "stage=workflow_navigation outcome=RETRY %s", diagnostics
                )

    async def _goto_route_via_home(self, page: Any, route: str, *, reload: bool = False) -> None:
        self._current_route = None
        await page.goto(
            workflow_url(self._base_url, _HOME_ROUTE), wait_until="domcontentloaded", timeout=60_000
        )
        await page.goto(
            workflow_url(self._base_url, route), wait_until="domcontentloaded", timeout=60_000
        )
        if reload:
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
        self._current_route = route

    async def _queue_diagnostics(self, page: Any, definition: WorkflowDefinition) -> str:
        """Safe facts about a view that did not render: no HTML, cookies or customer data."""
        root = definition.root_selector
        spec = definition.filter
        try:
            root_found = await page.locator(root).count() > 0
            filter_found = (
                None if spec is None else await page.locator(f"{root} {spec.control_selector}").count() > 0
            )
            header_found = await page.locator(f"{root} table thead th").count() > 0
            search_found = await page.locator(_search_submit_selector(root)).count() > 0
            view_ids = await page.evaluate(_VIEW_IDS_JS)
            url = strip_query(page.url) if isinstance(getattr(page, "url", None), str) else "?"
        except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the real failure
            return f"workflow={definition.key} (diagnostic indisponible: {type(exc).__name__})"
        ids = sorted(str(v) for v in view_ids) if isinstance(view_ids, list) else []
        return (
            f"workflow={definition.key} route={definition.route} url={url} root={root} "
            f"root_present={root_found} filter_present={filter_found} "
            f"search_button_present={search_found} header_present={header_found} views={ids}"
        )

    async def read_dossier_detail_fields(
        self, dossier: PortalDossierRef, fields: Sequence[FieldSpec]
    ) -> DossierDetailValues:
        """Reads the shared dossier detail page once per record per session.

        The detail page is the same whichever queue linked to it, so the values
        are cached by record id: reading a dossier for a second workflow costs
        no browser navigation.
        """
        cached = self._detail_cache.get(dossier.record_id)
        if cached is not None:
            return cached
        page = self._require_page()
        target = urljoin(f"{self._base_url.rstrip('/')}/", dossier.details_href)
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            await self._assert_authenticated()
            await page.locator(_DETAIL_READY_SELECTOR).first.wait_for(timeout=30_000)
            await self._settle(page)
            html = await page.content()
            await self._assert_authenticated()
        except PortalAuthRequiredError:
            raise
        except Exception as exc:
            raise DetailReadError(
                f"Lecture du détail impossible pour {dossier.record_id} ({type(exc).__name__})."
            ) from exc
        # A detail visit leaves the SPA on a dossier page: the next queue read must navigate.
        self._current_route = None
        result = DossierDetailValues(values=parse_detail_fields(html, fields))
        self._detail_cache[dossier.record_id] = result
        return result

__all__ = [
    "BrowserProfileLockedError",
    "CamoufoxPortalReader",
    "CamoufoxPortalReaderFactory",
]
