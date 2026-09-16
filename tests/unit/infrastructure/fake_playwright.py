"""Minimal Playwright ``Page``/``Locator`` test doubles.

Only the handful of methods ``CamoufoxPortalReader`` actually calls are
implemented. This verifies our own call-sequencing and argument-passing
logic (exact route, ``force=True`` on the hidden Chosen select, no
mandatory row wait, ...) without a real browser -- it is not a substitute
for verifying actual DOM/event behaviour against a live OmegaFlow session
(see docs/omegaflow-contract.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class FakeTimeoutError(Exception):
    pass


@dataclass
class FakeLocator:
    page: FakePage
    selector: str

    @property
    def first(self) -> FakeLocator:
        return self

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.page, f"{self.selector} {selector}")

    def get_by_text(self, pattern: Any) -> FakeLocator:
        return FakeLocator(self.page, f"{self.selector} :text")

    async def count(self) -> int:
        return self.page.counts.get(self.selector, 0)

    async def is_visible(self) -> bool:
        return self.selector in self.page.visible

    async def wait_for(self, state: str | None = None, timeout: float | None = None) -> None:
        self.page.wait_for_calls.append((self.selector, state, timeout))
        if state == "detached":
            return
        if self.selector not in self.page.existing:
            raise FakeTimeoutError(f"locator not found: {self.selector!r}")

    async def click(self) -> None:
        self.page.clicked.append(self.selector)

    async def select_option(
        self,
        value: str | None = None,
        *,
        label: str | None = None,
        force: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        self.page.select_option_calls.append(
            {"selector": self.selector, "value": value, "force": force, "timeout": timeout}
        )
        if self.page.select_option_effect is not None:
            self.page.select_option_effect(self, value)
        else:
            self.page.procedure_value = value

    async def input_value(self) -> str:
        return self.page.procedure_value

    async def evaluate(self, script: str) -> None:
        self.page.evaluate_calls.append((self.selector, script))


@dataclass
class FakePage:
    content_html: str = ""
    existing: set[str] = field(default_factory=set)
    visible: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    procedure_value: str = ""
    select_option_effect: Callable[[FakeLocator, str | None], None] | None = None

    goto_calls: list[str] = field(default_factory=list)
    clicked: list[str] = field(default_factory=list)
    select_option_calls: list[dict[str, Any]] = field(default_factory=list)
    evaluate_calls: list[tuple[str, str]] = field(default_factory=list)
    wait_for_calls: list[tuple[str, str | None, float | None]] = field(default_factory=list)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def goto(self, url: str, wait_until: str | None = None, timeout: float | None = None) -> None:
        self.goto_calls.append(url)

    async def content(self) -> str:
        return self.content_html

    async def wait_for_load_state(self, state: str | None = None, timeout: float | None = None) -> None:
        return None

    async def wait_for_timeout(self, timeout: float) -> None:
        return None

    async def wait_for_function(self, fn: str, arg: Any = None, timeout: float | None = None) -> None:
        return None
