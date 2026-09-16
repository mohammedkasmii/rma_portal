"""Regression tests for the camoufox_reader fixes.

These drive ``CamoufoxPortalReader`` against a fake Playwright ``Page``
(injected directly as ``reader._page``, bypassing the real Camoufox launch
entirely) to verify our own call-sequencing and argument-passing logic.
They do not exercise a real browser or the real Chosen widget -- see
docs/omegaflow-contract.md for what still needs live-session confirmation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rma_portal.application.dto import PortalReadError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReader
from tests.unit.infrastructure.fake_playwright import FakePage

PROCEDURE_VALUE = "5ed644a2faf17c0015d8c367"
START_ROUTE = Settings().omegaflow_start_route

ZERO_ROW_HTML = '<html><body><div id="view_1874"><table><tbody></tbody></table></div></body></html>'


def _make_reader() -> CamoufoxPortalReader:
    return CamoufoxPortalReader(
        profile_dir=Path("unused-profile-dir"),
        lock_path=Path("unused-lock-path"),
        start_route=START_ROUTE,
        base_url="https://omegaflow.ma/",
        procedure_value=PROCEDURE_VALUE,
        timezone_id="Africa/Casablanca",
        locale="fr-FR",
        headless=True,
    )


def _base_page(content_html: str) -> FakePage:
    return FakePage(
        content_html=content_html,
        existing={"#view_1874", "#view_1874 #kn-conn-1-field_219"},
        counts={"#view_1874 #kn-submit-filters": 1},
        visible={"#view_1874 #kn-submit-filters"},
    )


def test_start_route_has_no_slash_after_hash():
    assert START_ROUTE == "https://omegaflow.ma/#dossiers-en-instance-accord/"
    assert "#/" not in START_ROUTE


@pytest.mark.asyncio
async def test_navigates_to_the_exact_no_slash_start_route():
    reader = _make_reader()
    reader._page = _base_page(ZERO_ROW_HTML)

    await reader.read_agreement_queue()

    assert reader._page.goto_calls == [START_ROUTE]


@pytest.mark.asyncio
async def test_selects_hidden_chosen_procedure_with_force_and_verifies_native_value():
    reader = _make_reader()
    page = _base_page(ZERO_ROW_HTML)
    reader._page = page

    await reader.read_agreement_queue()

    assert len(page.select_option_calls) == 1
    call = page.select_option_calls[0]
    assert call["selector"] == "#view_1874 #kn-conn-1-field_219"
    assert call["value"] == PROCEDURE_VALUE
    assert call["force"] is True
    assert call["timeout"]
    assert page.procedure_value == PROCEDURE_VALUE
    assert any("dispatchEvent" in script for _, script in page.evaluate_calls)
    assert any("input" in script for _, script in page.evaluate_calls)
    assert any("change" in script for _, script in page.evaluate_calls)
    assert page.clicked == ["#view_1874 #kn-submit-filters"]


@pytest.mark.asyncio
async def test_procedure_value_mismatch_after_select_raises_portal_read_error():
    reader = _make_reader()
    page = _base_page(ZERO_ROW_HTML)
    # Simulate select_option appearing to run but the native value never
    # actually changing (e.g. Chosen intercepting the interaction).
    page.select_option_effect = lambda locator, value: None
    reader._page = page

    with pytest.raises(PortalReadError):
        await reader.read_agreement_queue()


@pytest.mark.asyncio
async def test_successful_filtered_queue_with_zero_rows_is_a_complete_snapshot():
    reader = _make_reader()
    reader._page = _base_page(ZERO_ROW_HTML)

    snapshot = await reader.read_agreement_queue()

    assert snapshot.rows == ()
    assert snapshot.pages_seen == 1


@pytest.mark.asyncio
async def test_read_agreement_queue_never_waits_for_a_row_to_exist():
    reader = _make_reader()
    page = _base_page(ZERO_ROW_HTML)
    reader._page = page

    await reader.read_agreement_queue()

    assert not any(
        "table tbody tr" in selector for selector, _state, _timeout in page.wait_for_calls
    )
