"""A stateful Playwright ``Page`` double that behaves like a tiny Knack site.

It models only what the generic reader depends on: routes hosting one or more
``#view_N`` tables, dropdown/next pagination, the Chosen-hidden procedure
filter with its search form, dossier detail pages and the (deliberate) fact
that a hash-only navigation to the route already displayed does not reload
the scene. Rendering goes through ``tests.parser.workflow_html`` so the reader
parses the same markup the parser tests use.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from rma_portal.domain.workflow_definition import WorkflowDefinition
from tests.parser.workflow_html import render_detail, render_page, render_view_body

LOGIN_HTML = '<html><body><div class="kn-login-form"><input type="password"></div></body></html>'


class FakeTimeoutError(Exception):
    pass


@dataclass
class FakeQueue:
    """One view's data. ``pages`` is a list of pages, each a list of synthetic rows."""

    definition: WorkflowDefinition
    pages: list[list[dict]] = field(default_factory=lambda: [[]])
    by_filter: dict[str, list[list[dict]]] = field(default_factory=dict)
    dropdown_pages: int | None = None
    """Options listed in the dropdown; fewer than ``len(pages)`` simulates a queue that
    grew while being read (the next control stays enabled)."""
    stale_pages: frozenset[int] = frozenset()
    """1-based page numbers that keep showing the previous page after navigation."""
    include_header: bool = True
    header_after_filter: bool = False
    """The shared agreement view: its table (and header) only render once the filter is submitted."""
    login_required: bool = False
    controls_missing_loads: int = 0
    """Scene builds (navigation or reload) that render the root without its filter controls."""
    # runtime state
    controls_ready: bool = True
    root_ready: bool = True
    missing_left: int = -1
    page_index: int = 0
    active_filter: str | None = None
    pending_filter: str = ""

    def visible_pages(self) -> list[list[dict]]:
        if self.definition.filter is not None:
            if self.active_filter is None:
                return [[]]
            return self.by_filter.get(self.active_filter, [[]])
        return self.pages

    @property
    def header_visible(self) -> bool:
        if not self.include_header:
            return False
        return not self.header_after_filter or self.active_filter is not None

    def reset(self, *, dirty: bool = False) -> None:
        """A (re)built scene. ``dirty`` = reached straight from a dossier detail page."""
        if self.missing_left < 0:
            self.missing_left = self.controls_missing_loads
        self.page_index = 0
        self.active_filter = None
        self.pending_filter = ""
        self.root_ready = not dirty
        self.controls_ready = not dirty and self.missing_left <= 0
        if self.missing_left > 0:
            self.missing_left -= 1

    def rows(self) -> list[dict]:
        pages = self.visible_pages()
        return pages[min(self.page_index, len(pages) - 1)]

    def render(self) -> str:
        pages = self.visible_pages()
        listed = self.dropdown_pages if self.dropdown_pages is not None else len(pages)
        last_shown = self.page_index + 1 >= len(pages)
        return render_view_body(
            self.definition,
            self.rows(),
            total_pages=listed,
            next_disabled=last_shown,
            include_header=self.header_visible,
            pagination=len(pages) > 1 or listed > 1,
        )


@dataclass
class FakeSite:
    routes: dict[str, list[FakeQueue]]
    details: dict[str, dict[str, str]] = field(default_factory=dict)
    detail_failures: set[str] = field(default_factory=set)
    details_route_prefix: str = "view-dossier-details/"


class _Locator:
    def __init__(self, page: FakeKnackPage, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> _Locator:
        return self

    def locator(self, selector: str) -> _Locator:
        return _Locator(self.page, f"{self.selector} {selector}")

    async def count(self) -> int:
        return self.page.count(self.selector)

    async def is_visible(self) -> bool:
        return self.page.count(self.selector) > 0

    async def wait_for(self, state: str | None = None, timeout: float | None = None) -> None:
        if state == "detached":
            return
        if self.page.count(self.selector) == 0 and not self.page.is_control(self.selector):
            raise FakeTimeoutError(f"locator not found: {self.selector!r}")

    async def click(self) -> None:
        self.page.click(self.selector)

    async def select_option(self, value: str | None = None, **_: Any) -> None:
        self.page.select_option(self.selector, str(value))

    async def input_value(self) -> str:
        queue = self.page.queue_for_selector(self.selector)
        return queue.pending_filter if queue else ""

    async def evaluate(self, script: str) -> None:
        return None

    async def get_attribute(self, name: str) -> str | None:
        return None


class FakeKnackPage:
    def __init__(self, site: FakeSite) -> None:
        self.site = site
        self.current_route: str | None = None
        self.current_detail: str | None = None
        self.goto_calls: list[str] = []
        self.select_calls: list[tuple[str, str]] = []
        self.clicks: list[str] = []
        self.detail_visits: list[str] = []
        self.login_shown = False
        self.reload_calls = 0
        self.url = "about:blank"
        self.detail_scene_bug = True
        """Live behaviour: a queue reached straight from a detail page keeps a stale scene."""

    # -- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _route_of(url: str) -> str:
        return "#" + url.split("#", 1)[1] if "#" in url else ""

    def queues(self) -> list[FakeQueue]:
        if self.current_route is None:
            return []
        return self.site.routes.get(self.current_route, [])

    def queue_for_selector(self, selector: str) -> FakeQueue | None:
        match = re.match(r"#(view_\d+)", selector)
        if not match:
            return None
        for queue in self.queues():
            if queue.definition.view_id == match.group(1):
                return queue
        return None

    def is_control(self, selector: str) -> bool:
        return "kn-conn" in selector or "kn-search_form" in selector

    def count(self, selector: str) -> int:
        if self.current_detail is not None:
            return 1 if ".kn-detail-body" in selector else 0
        queue = self.queue_for_selector(selector)
        if queue is None:
            return 0
        if not queue.root_ready:
            return 0
        if selector == f"#{queue.definition.view_id}":
            return 1
        if selector.endswith("table thead th"):
            return len(queue.definition.list_fields) if queue.header_visible else 0
        if "kn-conn" in selector or "kn-search_form" in selector:
            return 1 if queue.definition.filter is not None and queue.controls_ready else 0
        return 0

    def click(self, selector: str) -> None:
        self.clicks.append(selector)
        queue = self.queue_for_selector(selector)
        if queue is None:
            return
        if "kn-search_form" in selector:
            queue.active_filter = queue.pending_filter
            queue.page_index = 0
        elif "kn-next" in selector:
            queue.page_index = min(queue.page_index + 1, len(queue.visible_pages()) - 1)

    def select_option(self, selector: str, value: str) -> None:
        self.select_calls.append((selector, value))
        queue = self.queue_for_selector(selector)
        if queue is None:
            return
        if "kn-page-select" in selector:
            number = int(value)
            if number not in queue.stale_pages:
                queue.page_index = number - 1
        else:
            queue.pending_filter = value

    # -- Playwright surface used by CamoufoxPortalReader ---------------------------------------
    def locator(self, selector: str) -> _Locator:
        return _Locator(self, selector)

    async def goto(self, url: str, wait_until: str | None = None, timeout: float | None = None) -> None:
        self.goto_calls.append(url)
        self.url = url
        route = self._route_of(url)
        marker = self.site.details_route_prefix
        if marker in route:
            record_id = route.rstrip("/").rsplit("/", 1)[-1]
            self.current_detail = record_id
            self.current_route = route  # the SPA now shows the dossier scene
            self.detail_visits.append(record_id)
            if record_id in self.site.detail_failures:
                raise FakeTimeoutError("detail page did not render")
            return
        came_from_detail = self.current_detail is not None
        self.current_detail = None
        if route == self.current_route:
            return  # hash-only navigation to the displayed route: no reload, state persists
        self.current_route = route
        for queue in self.queues():
            queue.reset(dirty=came_from_detail and self.detail_scene_bug)

    async def reload(self, wait_until: str | None = None, timeout: float | None = None) -> None:
        self.reload_calls += 1
        for queue in self.queues():
            queue.reset()

    async def content(self) -> str:
        if any(queue.login_required for queue in self.queues()) or self.login_shown:
            return LOGIN_HTML
        if self.current_detail is not None:
            return render_detail(self.site.details.get(self.current_detail, {}))
        return render_page(*(queue.render() for queue in self.queues()))

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if "id^=" in script:  # the reader's rendered-view-ids diagnostic
            return [q.definition.view_id for q in self.queues() if q.root_ready]
        match = re.search(r"#(view_\d+)", script)
        if match:
            for queue in self.queues():
                if queue.definition.view_id == match.group(1):
                    return ",".join(sorted(row["id"] for row in queue.rows()))
        return ""

    async def wait_for_load_state(self, state: str | None = None, timeout: float | None = None) -> None:
        return None

    async def wait_for_timeout(self, timeout: float) -> None:
        await asyncio.sleep(0)

    async def wait_for_function(self, fn: str, arg: Any = None, timeout: float | None = None) -> None:
        return None
