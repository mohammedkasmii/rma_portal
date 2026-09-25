"""Generic multi-workflow reader: pagination, isolation, dedupe and the read-only guard."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from rma_portal.application.dto import PortalDossierRef
from rma_portal.domain.enums import PollStatus
from rma_portal.infrastructure.portal import camoufox_reader
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReader, workflow_url
from rma_portal.infrastructure.portal.workflow_catalog import SHARED_DETAIL_FIELDS, default_catalog
from tests.parser.workflow_html import synthetic_row
from tests.unit.infrastructure.fake_knack import FakeKnackPage, FakeQueue, FakeSite

BASE_URL = "https://omegaflow.example"
CATALOG = default_catalog()


def _reader(site: FakeSite) -> tuple[CamoufoxPortalReader, FakeKnackPage]:
    reader = CamoufoxPortalReader(
        profile_dir=Path("unused-profile"),
        lock_path=Path("unused-lock"),
        start_route=f"{BASE_URL}/#dossiers-en-instance-accord/",
        base_url=BASE_URL,
        timezone_id="Africa/Casablanca",
        session_state_path=Path("unused-state"),
        locale="fr-FR",
        headless=True,
    )
    page = FakeKnackPage(site)
    reader._page = page
    return reader, page


def _rows(prefix: str, count: int, start: int = 0) -> list[dict]:
    return [
        synthetic_row(f"{prefix}{n:03d}", dossier_number=f"D-{n}", portal_status="En cours")
        for n in range(start, start + count)
    ]


def _definition(key: str):
    definition = CATALOG.get(key)
    assert definition is not None
    return definition


@pytest.fixture(autouse=True)
def _fast_timeouts(monkeypatch):
    monkeypatch.setattr(camoufox_reader, "_QUEUE_VIEW_TIMEOUT_SECONDS", 0.05)


def test_workflow_url_uses_the_exact_captured_route_without_a_slash_after_the_hash():
    assert (
        workflow_url("https://omegaflow.ma/", "#dossiers-en-instance-photos/")
        == "https://omegaflow.ma/#dossiers-en-instance-photos/"
    )


@pytest.mark.asyncio
async def test_reads_every_page_dynamically_and_deduplicates_by_record_id():
    photos = _definition("photos_pending")
    # Page 3 repeats one record already seen on page 2 (a row shifting between pages).
    pages = [_rows("a", 4), _rows("b", 4), [*_rows("b", 1, 3), *_rows("c", 3)]]
    queue = FakeQueue(photos, pages=pages)
    reader, page = _reader(FakeSite({photos.route: [queue]}))

    outcome = await reader.read_workflow(photos)

    assert outcome.status is PollStatus.COMPLETE
    assert outcome.snapshot.pages_seen == 3
    ids = [row.record_id for row in outcome.snapshot.rows]
    assert len(ids) == len(set(ids)) == 4 + 4 + 3
    assert page.goto_calls == [workflow_url(BASE_URL, "#accueil/"), workflow_url(BASE_URL, photos.route)]
    assert [call[1] for call in page.select_calls] == ["2", "3"]


@pytest.mark.asyncio
async def test_rows_carry_the_definition_field_keys():
    photos = _definition("photos_pending")
    queue = FakeQueue(photos, pages=[[synthetic_row("rec1", portal_status="Instance", date_envoi="01/09/2026")]])
    reader, _ = _reader(FakeSite({photos.route: [queue]}))

    outcome = await reader.read_workflow(photos)

    (row,) = outcome.snapshot.rows
    assert row.fields["portal_status"] == "Instance"
    assert row.fields["date_envoi"] == "01/09/2026"
    assert row.details_href == "#dossiers-en-instance-photos/view-dossier-details/rec1/"


@pytest.mark.asyncio
async def test_empty_captured_queue_completes_with_zero_rows():
    empty = _definition("collegial_second_agreed")
    reader, _ = _reader(FakeSite({empty.route: [FakeQueue(empty, pages=[[]])]}))

    outcome = await reader.read_workflow(empty)

    assert outcome.status is PollStatus.COMPLETE
    assert outcome.snapshot.rows == ()
    assert outcome.error is None


@pytest.mark.asyncio
async def test_a_view_that_never_renders_its_table_is_failed_not_an_empty_success():
    definition = _definition("reserves_pending")
    queue = FakeQueue(definition, pages=[[]], include_header=False)
    reader, _ = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.FAILED
    assert outcome.snapshot.rows == ()


def _spy_readiness(reader: CamoufoxPortalReader) -> list[tuple]:
    """Records, in order, every readiness wait and every filter application."""
    events: list[tuple] = []
    wait, apply = reader._wait_for_queue_view, reader._apply_filter

    async def spy_wait(page, *args, require_table_header=False, **kwargs):
        events.append(("wait", require_table_header, kwargs.get("require_selector") is not None))
        await wait(page, *args, require_table_header=require_table_header, **kwargs)

    async def spy_apply(*args, **kwargs):
        events.append(("filter",))
        await apply(*args, **kwargs)

    reader._wait_for_queue_view = spy_wait  # type: ignore[method-assign]
    reader._apply_filter = spy_apply  # type: ignore[method-assign]
    return events


def _agreement_site(definition, *, by_filter, **queue_options):
    queue = FakeQueue(definition, by_filter=by_filter, header_after_filter=True, **queue_options)
    return FakeSite({definition.route: [queue]})


@pytest.mark.asyncio
async def test_a_filtered_workflow_opens_with_the_root_visible_and_no_table_header_yet():
    definition = _definition("agreement_garage")
    reader, page = _reader(_agreement_site(definition, by_filter={definition.filter.value: [_rows("g", 2)]}))

    await reader._open_workflow_view(page, definition)  # must not wait for a header that needs the filter

    assert await page.locator(f"{definition.root_selector} table thead th").count() == 0


@pytest.mark.asyncio
async def test_the_filter_is_applied_before_the_table_header_is_required():
    definition = _definition("agreement_garage")
    reader, _ = _reader(_agreement_site(definition, by_filter={definition.filter.value: [_rows("g", 2)]}))
    events = _spy_readiness(reader)

    await reader.read_workflow(definition)

    # root + filter control first (no header yet), then the header once the filter is submitted
    assert events == [("wait", False, True), ("filter",), ("wait", True, False)]


@pytest.mark.asyncio
async def test_the_header_appearing_after_submission_proceeds_to_parsing():
    definition = _definition("agreement_garage")
    reader, _ = _reader(
        _agreement_site(definition, by_filter={definition.filter.value: [_rows("g", 3), _rows("h", 2)]})
    )

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert len(outcome.snapshot.rows) == 5


@pytest.mark.asyncio
async def test_an_empty_filtered_table_is_complete_with_zero_rows():
    definition = _definition("agreement_hifad")
    reader, _ = _reader(_agreement_site(definition, by_filter={definition.filter.value: [[]]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert outcome.snapshot.rows == ()
    assert outcome.error is None


@pytest.mark.asyncio
async def test_a_filtered_view_whose_header_never_renders_after_submission_is_failed():
    definition = _definition("agreement_normal")
    site = _agreement_site(definition, by_filter={definition.filter.value: [[]]}, include_header=False)
    reader, _ = _reader(site)

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.FAILED


@pytest.mark.asyncio
async def test_an_unfiltered_workflow_still_requires_the_table_header_during_navigation():
    definition = _definition("reserves_pending")
    assert definition.filter is None
    reader, page = _reader(FakeSite({definition.route: [FakeQueue(definition, pages=[[]], include_header=False)]}))
    events = _spy_readiness(reader)

    with pytest.raises(camoufox_reader.PortalReadError):
        await reader._open_workflow_view(page, definition)

    assert events == [("wait", True, False)] * 2  # the header stays required; one bounded retry


@pytest.mark.asyncio
async def test_auth_required_is_unchanged_for_a_filtered_workflow():
    definition = _definition("agreement_collegial_cid")
    reader, _ = _reader(
        _agreement_site(definition, by_filter={definition.filter.value: [_rows("c", 1)]}, login_required=True)
    )

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.AUTH_REQUIRED
    assert outcome.snapshot.rows == ()


def _detail_ref(record_id: str = "rec-detail"):
    return PortalDossierRef(
        record_id=record_id,
        details_href=f"#view-dossier-details/{record_id}/",
    )


async def _enrich_one_detail(reader: CamoufoxPortalReader, site: FakeSite, record_id: str = "rec-detail"):
    site.details[record_id] = {}
    await reader.read_dossier_detail_fields(_detail_ref(record_id), SHARED_DETAIL_FIELDS)
    assert reader._current_route is None  # the browser is left on a dossier page


@pytest.mark.asyncio
async def test_garage_then_detail_enrichment_then_procedure_normale_still_finds_its_filter():
    garage = _definition("agreement_garage")
    normal = _definition("agreement_normal")
    queue = FakeQueue(
        garage,
        by_filter={garage.filter.value: [_rows("g", 2)], normal.filter.value: [_rows("n", 3)]},
        header_after_filter=True,
    )
    site = FakeSite({garage.route: [queue]})
    reader, page = _reader(site)

    first = await reader.read_workflow(garage)
    await _enrich_one_detail(reader, site)
    second = await reader.read_workflow(normal)

    assert first.status is PollStatus.COMPLETE
    assert second.status is PollStatus.COMPLETE
    assert len(second.snapshot.rows) == 3
    assert page.goto_calls[-2:] == [workflow_url(BASE_URL, "#accueil/"), workflow_url(BASE_URL, garage.route)]


@pytest.mark.asyncio
async def test_sequential_shared_route_agreement_filters_each_start_from_a_fresh_scene():
    keys = ["agreement_garage", "agreement_normal", "agreement_appreciation", "agreement_collegial_cid", "agreement_hifad"]
    definitions = [_definition(key) for key in keys]
    queue = FakeQueue(
        definitions[0],
        by_filter={d.filter.value: [_rows(d.key[10:13], 2)] for d in definitions},
        header_after_filter=True,
    )
    site = FakeSite({definitions[0].route: [queue]})
    reader, _ = _reader(site)

    outcomes = {}
    for definition in definitions:
        outcomes[definition.key] = await reader.read_workflow(definition)
        await _enrich_one_detail(reader, site, f"rec-{definition.key}")

    assert {k: o.status for k, o in outcomes.items()} == dict.fromkeys(keys, PollStatus.COMPLETE)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["hifad_search", "report_pending"])
async def test_a_queue_read_right_after_a_detail_page_is_reached_through_the_home_route(key):
    definition = _definition(key)
    rows = [_rows("x", 2)]
    queue = FakeQueue(definition, pages=rows, by_filter={definition.filter.value: rows} if definition.filter else {})
    site = FakeSite({definition.route: [queue]})
    reader, page = _reader(site)
    await _enrich_one_detail(reader, site)

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert len(outcome.snapshot.rows) == 2
    assert page.goto_calls[-2] == workflow_url(BASE_URL, "#accueil/")
    assert page.reload_calls == 0  # no recovery was needed


@pytest.mark.asyncio
async def test_one_bounded_recovery_reloads_the_target_when_the_filter_control_is_missing():
    definition = _definition("agreement_normal")
    queue = FakeQueue(
        definition,
        by_filter={definition.filter.value: [_rows("n", 2)]},
        header_after_filter=True,
        controls_missing_loads=1,  # the first scene build renders the root without its controls
    )
    reader, page = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert page.reload_calls == 1


@pytest.mark.asyncio
async def test_a_second_failure_is_final_after_exactly_one_retry_with_safe_diagnostics(caplog):
    definition = _definition("agreement_normal")
    queue = FakeQueue(
        definition,
        by_filter={definition.filter.value: [_rows("n", 2)]},
        header_after_filter=True,
        controls_missing_loads=99,
    )
    reader, page = _reader(FakeSite({definition.route: [queue]}))
    caplog.set_level("INFO")

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.FAILED
    assert page.reload_calls == 1  # bounded: one recovery, never a loop
    assert page.goto_calls.count(workflow_url(BASE_URL, "#accueil/")) == 2
    error = outcome.error or ""
    assert "Délai dépassé" in error
    assert f"workflow={definition.key}" in error
    assert f"route={definition.route}" in error
    assert f"root={definition.root_selector}" in error
    assert "root_present=True" in error and "filter_present=False" in error and "header_present=False" in error
    assert "views=['view_1874']" in error
    assert "?" not in error.split("Diagnostic :")[1].split("url=")[1].split(" ")[0]  # URL carries no query
    assert any("outcome=RETRY" in r.message for r in caplog.records)


def _search_clicks(page) -> list[str]:
    return [c for c in page.clicks if "kn-search_form" in c]


def test_only_the_two_search_first_views_submit_their_search_on_open():
    flagged = {d.key for d in CATALOG.definitions() if d.submit_search_on_open}
    assert flagged == {"hifad_search", "report_pending"}
    assert all(_definition(key).filter is None for key in flagged)  # the model never mixes both


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["hifad_search", "report_pending"])
async def test_a_search_first_workflow_submits_its_initial_search_once_and_reads_rows(key):
    definition = _definition(key)
    queue = FakeQueue(definition, pages=[_rows("s", 3), _rows("t", 2)], header_after_filter=True)
    reader, page = _reader(FakeSite({definition.route: [queue]}))
    events = _spy_readiness(reader)

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert len(outcome.snapshot.rows) == 5
    assert len(_search_clicks(page)) == 1  # submitted exactly once, without touching any filter
    assert not [c for c in page.select_calls if "kn-conn" in c[0]]
    # root + search button first (no header), then the header once the search has run
    assert events == [("wait", False, True), ("wait", True, False)]


@pytest.mark.asyncio
async def test_an_empty_search_first_result_is_complete_with_zero_rows():
    definition = _definition("report_pending")
    queue = FakeQueue(definition, pages=[[]], header_after_filter=True)
    reader, page = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert outcome.snapshot.rows == ()
    assert len(_search_clicks(page)) == 1


@pytest.mark.asyncio
async def test_an_ordinary_unfiltered_workflow_never_submits_a_search():
    definition = _definition("photos_pending")
    reader, page = _reader(FakeSite({definition.route: [FakeQueue(definition, pages=[_rows("p", 2)])]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert _search_clicks(page) == []


@pytest.mark.asyncio
async def test_a_filtered_agreement_workflow_still_selects_its_filter_and_submits_once():
    definition = _definition("agreement_normal")
    queue = FakeQueue(definition, by_filter={definition.filter.value: [_rows("n", 2)]}, header_after_filter=True)
    reader, page = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert [v for s_, v in page.select_calls if "kn-conn" in s_] == [definition.filter.value]
    assert len(_search_clicks(page)) == 1


@pytest.mark.asyncio
async def test_a_missing_root_keeps_the_full_timeout_and_a_missing_child_control_recovers_sooner(monkeypatch):
    monkeypatch.setattr(camoufox_reader, "_QUEUE_VIEW_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(camoufox_reader, "_CHILD_CONTROL_TIMEOUT_SECONDS", 0.02)
    definition = _definition("agreement_normal")
    rows = {definition.filter.value: [_rows("n", 1)]}

    no_root = FakeQueue(definition, by_filter=rows, header_after_filter=True, root_missing=True)
    reader, _ = _reader(FakeSite({definition.route: [no_root]}))
    started = time.monotonic()
    missing_root = await reader.read_workflow(definition)
    root_elapsed = time.monotonic() - started

    no_child = FakeQueue(definition, by_filter=rows, header_after_filter=True, controls_missing_loads=99)
    reader, page = _reader(FakeSite({definition.route: [no_child]}))
    started = time.monotonic()
    missing_child = await reader.read_workflow(definition)
    child_elapsed = time.monotonic() - started

    assert missing_root.status is PollStatus.FAILED and missing_child.status is PollStatus.FAILED
    assert "affichage de la file, après 0s" in missing_root.error  # the full queue timeout message
    assert root_elapsed >= 0.55  # two attempts, each waiting the full timeout
    assert "contrôles de la file" in missing_child.error
    assert child_elapsed < 0.25  # the short bound, twice (one bounded recovery)
    assert page.reload_calls == 1


@pytest.mark.asyncio
async def test_pagination_continues_while_the_next_control_stays_enabled():
    """The queue grew while being read: the dropdown lists 2 pages, a 3rd exists."""
    definition = _definition("reformed")
    queue = FakeQueue(
        definition, pages=[_rows("a", 3), _rows("b", 3), _rows("c", 2)], dropdown_pages=2
    )
    reader, page = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.COMPLETE
    assert len(outcome.snapshot.rows) == 8
    assert page.select_calls and page.select_calls[-1][1] == "2"
    assert any("kn-next" in click for click in page.clicks)  # page 3 reached via "next"


@pytest.mark.asyncio
async def test_a_stale_page_keeps_the_rows_read_so_far_as_partial():
    definition = _definition("agreement_validated")
    queue = FakeQueue(
        definition,
        pages=[_rows("a", 3), _rows("b", 3), _rows("c", 3)],
        stale_pages=frozenset({3}),
    )
    reader, _ = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.PARTIAL
    assert len(outcome.snapshot.rows) == 6
    assert "obsol" in (outcome.error or "")


@pytest.mark.asyncio
async def test_login_screen_is_an_auth_required_outcome_without_rows():
    definition = _definition("hifad_search")
    queue = FakeQueue(definition, pages=[_rows("a", 2)], login_required=True)
    reader, _ = _reader(FakeSite({definition.route: [queue]}))

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.AUTH_REQUIRED
    assert outcome.snapshot.rows == ()


@pytest.mark.asyncio
async def test_one_failed_workflow_never_stops_or_invalidates_the_others():
    first = _definition("photos_pending")
    broken = _definition("fft_garage_pending")
    third = _definition("hifad_search")
    site = FakeSite(
        {
            first.route: [FakeQueue(first, pages=[_rows("a", 2)])],
            broken.route: [FakeQueue(broken, pages=[[]], include_header=False)],
            third.route: [FakeQueue(third, pages=[_rows("c", 3)])],
        }
    )
    reader, _ = _reader(site)

    outcomes = await reader.read_workflows([first, broken, third])

    assert list(outcomes) == [first.key, broken.key, third.key]
    assert outcomes[first.key].status is PollStatus.COMPLETE
    assert outcomes[broken.key].status is PollStatus.FAILED
    assert outcomes[third.key].status is PollStatus.COMPLETE
    assert len(outcomes[third.key].snapshot.rows) == 3


@pytest.mark.asyncio
async def test_agreement_variants_share_a_route_and_each_gets_its_own_filter_and_fresh_page():
    garage = _definition("agreement_garage")
    normal = _definition("agreement_normal")
    hifad = _definition("agreement_hifad")
    assert garage.route == normal.route == hifad.route
    queue = FakeQueue(
        garage,  # one shared view_1874 on the route
        by_filter={
            garage.filter.value: [_rows("g", 3), _rows("h", 2)],
            normal.filter.value: [_rows("n", 4)],
            hifad.filter.value: [[]],  # captured empty result
        },
    )
    reader, page = _reader(FakeSite({garage.route: [queue]}))

    outcomes = await reader.read_workflows([garage, normal, hifad])

    assert [len(outcomes[k].snapshot.rows) for k in (garage.key, normal.key, hifad.key)] == [5, 4, 0]
    assert all(o.status is PollStatus.COMPLETE for o in outcomes.values())
    # Exact captured procedure option values, applied on the hidden Chosen select.
    filter_calls = [value for selector, value in page.select_calls if "kn-conn" in selector]
    assert filter_calls == [garage.filter.value, normal.filter.value, hifad.filter.value]
    # Every workflow goes home first so its scene is genuinely rebuilt, never a no-op hash jump.
    home = workflow_url(BASE_URL, "#accueil/")
    route = workflow_url(BASE_URL, garage.route)
    assert page.goto_calls == [home, route, home, route, home, route]


@pytest.mark.asyncio
async def test_two_views_on_one_route_are_read_independently():
    cancel = _definition("deficiency_cancel_appreciation")
    intermediary = _definition("deficiency_intermediary")
    assert cancel.route == intermediary.route
    site = FakeSite(
        {
            cancel.route: [
                FakeQueue(cancel, pages=[_rows("x", 3), _rows("y", 3)]),
                FakeQueue(intermediary, pages=[_rows("z", 2)]),
            ]
        }
    )
    reader, _ = _reader(site)

    outcomes = await reader.read_workflows([cancel, intermediary])

    assert len(outcomes[cancel.key].snapshot.rows) == 6
    assert len(outcomes[intermediary.key].snapshot.rows) == 2
    assert not (
        {r.record_id for r in outcomes[cancel.key].snapshot.rows}
        & {r.record_id for r in outcomes[intermediary.key].snapshot.rows}
    )


@pytest.mark.asyncio
async def test_filter_that_does_not_stick_is_a_failed_outcome():
    definition = _definition("agreement_normal")
    queue = FakeQueue(definition, by_filter={definition.filter.value: [_rows("n", 1)]})
    reader, page = _reader(FakeSite({definition.route: [queue]}))
    # Chosen swallowed the interaction: the native value never changes.
    original = page.select_option
    page.select_option = lambda selector, value: (
        page.select_calls.append((selector, value)) if "kn-conn" in selector else original(selector, value)
    )

    outcome = await reader.read_workflow(definition)

    assert outcome.status is PollStatus.FAILED
    assert "filtre" in (outcome.error or "").lower()


@pytest.mark.asyncio
async def test_detail_pages_are_read_once_per_record_across_workflows():
    photos = _definition("photos_pending")
    site = FakeSite(
        {photos.route: [FakeQueue(photos, pages=[[synthetic_row("rec1")]])]},
        details={"rec1": {"field_114": "12/09/2026", "field_852": "", "field_133": "13/09/2026"}},
    )
    reader, page = _reader(site)
    ref = PortalDossierRef("rec1", "#dossiers-en-instance-photos/view-dossier-details/rec1/")

    first = await reader.read_dossier_detail_fields(ref, SHARED_DETAIL_FIELDS)
    # The same dossier surfaced by another workflow links through a different route.
    second = await reader.read_dossier_detail_fields(
        PortalDossierRef("rec1", "#dossiers-en-instance-fft2/view-dossier-details/rec1/"),
        SHARED_DETAIL_FIELDS,
    )

    assert first is second
    assert page.detail_visits == ["rec1"]
    assert first.values["date_envoi_devis_garage"] == "12/09/2026"
    assert first.values["date_avis_dommage"] == ""  # shown but empty
    assert "date_creation" not in first.values  # not shown for this dossier


@pytest.mark.asyncio
async def test_detail_failure_is_a_detail_error_and_the_next_navigation_is_a_clean_load():
    from rma_portal.application.dto import DetailReadError

    photos = _definition("photos_pending")
    site = FakeSite(
        {photos.route: [FakeQueue(photos, pages=[[synthetic_row("rec1")]])]},
        detail_failures={"rec1"},
    )
    reader, page = _reader(site)

    with pytest.raises(DetailReadError):
        await reader.read_dossier_detail_fields(
            PortalDossierRef("rec1", "#x/view-dossier-details/rec1/"), SHARED_DETAIL_FIELDS
        )
    outcome = await reader.read_workflow(photos)

    assert outcome.status is PollStatus.COMPLETE
    assert page.goto_calls[-1] == workflow_url(BASE_URL, photos.route)


# --- read-only request guard -------------------------------------------------------------------


class _Route:
    def __init__(self) -> None:
        self.aborted: str | None = None
        self.continued = False

    async def abort(self, reason: str) -> None:
        self.aborted = reason

    async def continue_(self) -> None:
        self.continued = True


class _Request:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "blocked"),
    [
        ("GET", "https://omegaflow.ma/#dossiers-en-instance-photos/", False),
        ("GET", "https://api.knack.com/v1/scenes/scene_499/views/view_787/records?page=2", False),
        ("PUT", "https://api.knack.com/v1/scenes/scene_20/views/view_23/records/abc", True),
        ("PATCH", "https://omegaflow.ma/anything", True),
        ("DELETE", "https://api.knack.com/v1/objects/object_1/records/abc", True),
        ("POST", "https://api.knack.com/v1/scenes/scene_1/views/view_9/records", True),
        ("POST", "https://api.knack.com/v1/applications/xyz/session", False),
        ("PUT", "https://cdn.example.com/other-host", False),
    ],
)
async def test_read_only_route_blocks_writes_to_the_portal_only(method, url, blocked):
    reader, _ = _reader(FakeSite({}))
    route = _Route()

    await reader._read_only_route(route, _Request(method, url))

    assert (route.aborted == "blockedbyclient") is blocked
    assert route.continued is not blocked
    assert bool(reader.blocked_write_attempts) is blocked


@pytest.mark.asyncio
async def test_reading_a_whole_cycle_never_issues_a_click_that_is_not_navigation_or_search():
    """Only the captured search submit, dropdown selection and 'next' controls are touched."""
    garage = _definition("agreement_garage")
    queue = FakeQueue(garage, by_filter={garage.filter.value: [_rows("g", 2), _rows("h", 2)]})
    reader, page = _reader(FakeSite({garage.route: [queue]}))

    await reader.read_workflow(garage)

    assert page.clicks
    assert all("kn-search_form" in c or "kn-next" in c for c in page.clicks)


def test_reader_is_cancellable():
    """Cancellation must propagate (never be swallowed into a FAILED outcome)."""
    photos = _definition("photos_pending")

    class _Hanging(FakeKnackPage):
        async def goto(self, *args, **kwargs):
            await asyncio.sleep(10)

    reader, _ = _reader(FakeSite({photos.route: [FakeQueue(photos, pages=[[]])]}))
    reader._page = _Hanging(FakeSite({}))

    async def run():
        task = asyncio.create_task(reader.read_workflow(photos))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
