"""Regression tests for the camoufox_reader fixes.

These drive ``CamoufoxPortalReader`` against a fake Playwright ``Page``
(injected directly as ``reader._page``, bypassing the real Camoufox launch
entirely) to verify our own call-sequencing and argument-passing logic.
They do not exercise a real browser or the real Chosen widget -- see
docs/omegaflow-contract.md for what still needs live-session confirmation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from rma_portal.application.dto import PortalAuthRequiredError, PortalReadError
from rma_portal.config import Settings
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReader
from tests.unit.infrastructure.fake_playwright import FakePage

PROCEDURE_VALUE = "5ed644a2faf17c0015d8c367"
START_ROUTE = Settings().omegaflow_start_route

ZERO_ROW_HTML = '<html><body><div id="view_1874"><table><tbody></tbody></table></div></body></html>'
LOADING_SHELL_HTML = '<html><body><div id="knack-body">Loading...</div></body></html>'
LOGIN_FORM_HTML = '<html><body><input type="password"></body></html>'


def _make_reader(
    lock_path: Path = Path("unused-lock-path"),
    session_state_path: Path = Path("unused-session-state-path"),
) -> CamoufoxPortalReader:
    return CamoufoxPortalReader(
        profile_dir=Path("unused-profile-dir"),
        lock_path=lock_path,
        start_route=START_ROUTE,
        base_url="https://omegaflow.ma/",
        procedure_value=PROCEDURE_VALUE,
        timezone_id="Africa/Casablanca",
        session_state_path=session_state_path,
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


def _rendered_view_page(content_html: str) -> FakePage:
    """A page where the authenticated queue view (#view_1874) is already
    visible -- positive evidence, as opposed to just an absent login form."""
    return FakePage(content_html=content_html, counts={"#view_1874": 1}, visible={"#view_1874"})


@dataclass
class _EventuallyRendersTheView(FakePage):
    """Starts as a loading shell; #view_1874 becomes visible after
    ``polls_before_visible`` calls to ``wait_for_timeout`` (i.e. poll
    iterations of ``verify_authenticated``)."""

    polls_before_visible: int = 0

    async def wait_for_timeout(self, timeout: float) -> None:
        self.polls_before_visible -= 1
        if self.polls_before_visible <= 0:
            self.counts["#view_1874"] = 1
            self.visible.add("#view_1874")


@dataclass
class _EventuallyShowsALoginForm(FakePage):
    """Starts as a loading shell; a login form appears after
    ``polls_before_login`` poll iterations."""

    polls_before_login: int = 0

    async def wait_for_timeout(self, timeout: float) -> None:
        self.polls_before_login -= 1
        if self.polls_before_login <= 0:
            self.content_html = LOGIN_FORM_HTML


@pytest.mark.asyncio
async def test_verify_authenticated_never_resolves_a_perpetual_loading_shell():
    """Regression: an unauthenticated loading shell (neither a login
    marker nor the rendered queue view) must never be treated as
    authenticated -- verify_authenticated must stay pending, not return
    normally, so the caller's own bound (not this method) is what turns it
    into ERROR/timeout. If it returned normally here, that would be a
    false READY."""
    reader = _make_reader()
    reader._page = FakePage(content_html=LOADING_SHELL_HTML)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.verify_authenticated(), timeout=0.2)


@pytest.mark.asyncio
async def test_verify_authenticated_succeeds_once_the_queue_view_actually_renders():
    reader = _make_reader()
    reader._page = _EventuallyRendersTheView(
        content_html=LOADING_SHELL_HTML, polls_before_visible=3
    )

    await asyncio.wait_for(reader.verify_authenticated(), timeout=1.0)  # must not raise/time out


@pytest.mark.asyncio
async def test_verify_authenticated_raises_immediately_for_an_already_visible_login_form():
    reader = _make_reader()
    reader._page = FakePage(content_html=LOGIN_FORM_HTML)

    with pytest.raises(PortalAuthRequiredError):
        await asyncio.wait_for(reader.verify_authenticated(), timeout=0.2)


@pytest.mark.asyncio
async def test_verify_authenticated_keeps_checking_for_a_login_form_while_waiting():
    """Regression: a login form that only appears after the shell has been
    polling for a while must still be caught, not missed because the
    first check already passed."""
    reader = _make_reader()
    reader._page = _EventuallyShowsALoginForm(content_html=LOADING_SHELL_HTML, polls_before_login=2)

    with pytest.raises(PortalAuthRequiredError):
        await asyncio.wait_for(reader.verify_authenticated(), timeout=1.0)


@pytest.mark.asyncio
async def test_verify_authenticated_never_applies_the_filter_or_paginates():
    reader = _make_reader()
    page = _rendered_view_page(ZERO_ROW_HTML)
    reader._page = page

    await reader.verify_authenticated()

    assert page.select_option_calls == []
    assert page.clicked == []


class _StallingManager:
    """Fakes AsyncCamoufox's own browser-manager __aexit__ hanging forever
    (e.g. a stuck close), so CamoufoxPortalReader.__aexit__'s bounded
    caller (asyncio.wait_for) is what ends up cancelling it."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._gate.wait()


@pytest.mark.asyncio
async def test_aexit_keeps_the_profile_lock_when_manager_teardown_stalls(tmp_path):
    """Regression: authentication succeeding must not matter if teardown
    itself cannot be confirmed -- __aexit__ must never release the profile
    lock in that case (a fresh acquisition attempt on the same path must
    then fail fast, not race a browser that might still be running)."""
    from rma_portal.application.dto import BrowserProfileLockedError
    from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock

    lock_path = tmp_path / "profile.lock"
    reader = _make_reader(lock_path=lock_path)
    reader._lock_cm = acquire_profile_lock(lock_path)
    reader._lock = reader._lock_cm.__enter__()
    gate = asyncio.Event()  # deliberately never set: teardown stalls
    reader._manager = _StallingManager(gate)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.__aexit__(None, None, None), timeout=0.05)

    with (
        pytest.raises(BrowserProfileLockedError, match="indisponible"),
        acquire_profile_lock(lock_path),
    ):
        pass


@pytest.mark.asyncio
async def test_aexit_releases_the_profile_lock_when_manager_teardown_succeeds(tmp_path):
    """Sanity counterpart: a normal, confirmed teardown must still release
    the lock as before -- only an *unconfirmed* one keeps it held."""
    from rma_portal.infrastructure.portal.profile_lock import acquire_profile_lock

    lock_path = tmp_path / "profile.lock"
    reader = _make_reader(lock_path=lock_path)
    reader._lock_cm = acquire_profile_lock(lock_path)
    reader._lock = reader._lock_cm.__enter__()

    class _CleanManager:
        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    reader._manager = _CleanManager()

    await reader.__aexit__(None, None, None)

    with acquire_profile_lock(lock_path):  # must succeed -- lock was released
        pass
