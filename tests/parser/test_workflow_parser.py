"""Workflow HTML parsing against synthetic fixtures for every catalogued view."""

from __future__ import annotations

import pytest

from rma_portal.infrastructure.portal.parser import PortalMarkupError
from rma_portal.infrastructure.portal.workflow_catalog import SHARED_DETAIL_FIELDS, default_catalog
from rma_portal.infrastructure.portal.workflow_parser import (
    has_enabled_next,
    has_table_header,
    merge_workflow_snapshots,
    parse_detail_fields,
    parse_total_pages,
    parse_workflow_page,
)
from tests.parser.workflow_html import (
    render_detail,
    render_page,
    render_view,
    render_view_body,
    synthetic_row,
)

CATALOG = default_catalog()
ALL_DEFINITIONS = CATALOG.definitions()
PHOTOS = CATALOG.get("photos_pending")


@pytest.mark.parametrize("definition", ALL_DEFINITIONS, ids=lambda d: d.key)
def test_every_catalogued_view_round_trips_all_of_its_list_fields(definition):
    values = {spec.key: f"valeur {spec.css_class}" for spec in definition.list_fields}
    html = render_view(definition, [synthetic_row("rec00000000000000000001", **values)])

    snapshot = parse_workflow_page(html, definition)

    (row,) = snapshot.rows
    assert row.record_id == "rec00000000000000000001"
    assert row.fields == values
    assert snapshot.workflow_key == definition.key
    assert row.details_href.endswith("/view-dossier-details/rec00000000000000000001/")


def test_exact_details_link_wins_over_the_decoy_observation_link():
    html = render_view(PHOTOS, [synthetic_row("recA")])

    (row,) = parse_workflow_page(html, PHOTOS).rows

    assert "view-dossier-details7" not in row.details_href
    assert row.details_href.endswith("view-dossier-details/recA/")


def test_a_row_without_a_details_link_is_kept_with_an_empty_href():
    html = render_view(PHOTOS, [synthetic_row("recA")], details=False)

    (row,) = parse_workflow_page(html, PHOTOS).rows

    assert row.record_id == "recA"
    assert row.details_href == ""


def test_empty_table_with_header_is_a_valid_zero_row_page():
    html = render_view(PHOTOS, [])

    assert parse_workflow_page(html, PHOTOS).rows == ()
    assert has_table_header(html, PHOTOS)


def test_table_without_header_is_not_positive_evidence():
    html = render_view(PHOTOS, [], include_header=False)

    assert not has_table_header(html, PHOTOS)


def test_missing_view_root_is_a_markup_error():
    with pytest.raises(PortalMarkupError):
        parse_workflow_page(render_page("<div id='view_1'></div>"), PHOTOS)


def test_only_the_definitions_own_view_is_parsed_when_two_share_a_page():
    cancel = CATALOG.get("deficiency_cancel_appreciation")
    intermediary = CATALOG.get("deficiency_intermediary")
    html = render_page(
        render_view_body(cancel, [synthetic_row("cancel1")]),
        render_view_body(intermediary, [synthetic_row("inter1"), synthetic_row("inter2")]),
    )

    assert [r.record_id for r in parse_workflow_page(html, cancel).rows] == ["cancel1"]
    assert [r.record_id for r in parse_workflow_page(html, intermediary).rows] == ["inter1", "inter2"]


def test_duplicate_row_ids_inside_a_page_and_across_pages_collapse():
    page = render_view(PHOTOS, [synthetic_row("a"), synthetic_row("a"), synthetic_row("b")])
    first = parse_workflow_page(page, PHOTOS)
    second = parse_workflow_page(render_view(PHOTOS, [synthetic_row("b"), synthetic_row("c")]), PHOTOS)

    merged = merge_workflow_snapshots(PHOTOS, [first, second])

    assert [r.record_id for r in first.rows] == ["a", "b"]
    assert [r.record_id for r in merged.rows] == ["a", "b", "c"]
    assert merged.pages_seen == 2


def test_pagination_markup_page_count_and_next_control():
    single = render_view(PHOTOS, [synthetic_row("a")])
    middle = render_view(PHOTOS, [synthetic_row("a")], total_pages=5, next_disabled=False)
    last = render_view(PHOTOS, [synthetic_row("a")], total_pages=5, next_disabled=True)

    assert parse_total_pages(single, PHOTOS) == 1
    assert not has_enabled_next(single, PHOTOS)  # no widget at all
    assert parse_total_pages(middle, PHOTOS) == 5 and has_enabled_next(middle, PHOTOS)
    assert parse_total_pages(last, PHOTOS) == 5 and not has_enabled_next(last, PHOTOS)


def test_detail_fields_distinguish_absent_from_blank():
    html = render_detail({"field_114": "12/09/2026 10:30", "field_852": "", "field_1": "x"})

    values = parse_detail_fields(html, SHARED_DETAIL_FIELDS)

    assert values == {"date_envoi_devis_garage": "12/09/2026 10:30", "date_avis_dommage": ""}


def test_nested_detail_spans_are_flattened():
    html = render_detail({"field_133": "  13/09/2026 "})

    assert parse_detail_fields(html, SHARED_DETAIL_FIELDS) == {"date_envoi_facture": "13/09/2026"}
