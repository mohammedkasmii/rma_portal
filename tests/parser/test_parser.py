"""V1 (Garage agréé) fixtures parsed by the generic workflow parser.

The original synthetic fixtures describe the ``view_1874`` queue and the shared dossier
detail page. Running them through ``parse_workflow_page`` with the ``agreement_garage``
definition proves the generic parser is a drop-in replacement for the V1 one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rma_portal.domain.portal_dates import parse_french_date
from rma_portal.infrastructure.portal.parser import detect_auth_required
from rma_portal.infrastructure.portal.workflow_catalog import SHARED_DETAIL_FIELDS, default_catalog
from rma_portal.infrastructure.portal.workflow_parser import (
    merge_workflow_snapshots,
    parse_detail_fields,
    parse_total_pages,
    parse_workflow_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
GARAGE = default_catalog().get("agreement_garage")


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_every_list_field():
    snapshot = parse_workflow_page(_read("list_page_1.html"), GARAGE)
    row = next(r for r in snapshot.rows if r.record_id == "5f1a2b3c4d5e6f7a8b9c0d1f")
    assert row.fields["dossier_number"] == "DOS-0002"
    assert row.fields["insured_name"] == "Bilal Exemple"
    assert row.fields["procedure"] == "Garage agréé"
    assert row.fields["registration"] == "6789-B-12"
    assert row.fields["garage"] == "Garage Exemple Deux"
    assert row.fields["estimate_amount_raw"] == "8 500,00 MAD"
    assert row.fields["portal_status"] == "En cours"
    assert row.fields["city"] == "Rabat"
    assert row.fields["observation_count"] == "0"
    assert row.fields["agreement_login"] == "login-deux"


def test_stable_row_id_is_the_tr_id_attribute():
    snapshot = parse_workflow_page(_read("list_page_1.html"), GARAGE)
    ids = {row.record_id for row in snapshot.rows}
    assert "5f1a2b3c4d5e6f7a8b9c0d1e" in ids
    assert "5f1a2b3c4d5e6f7a8b9c0d1f" in ids


def test_exact_details_href_skips_the_decoy_observation_link():
    snapshot = parse_workflow_page(_read("list_page_1.html"), GARAGE)
    row = next(r for r in snapshot.rows if r.record_id == "5f1a2b3c4d5e6f7a8b9c0d1e")
    assert row.details_href == (
        "#dossiers-en-instance-accord/view-dossier-details/5f1a2b3c4d5e6f7a8b9c0d1e?ref=list_1874"
    )
    assert "view-dossier-details7" not in row.details_href


def test_duplicate_id_removed_within_one_page():
    snapshot = parse_workflow_page(_read("list_page_1.html"), GARAGE)
    ids = [row.record_id for row in snapshot.rows]
    assert ids.count("5f1a2b3c4d5e6f7a8b9c0d1e") == 1


def test_two_page_pagination_reports_two_pages():
    assert parse_total_pages(_read("list_page_1.html"), GARAGE) == 2


def test_merge_snapshots_deduplicates_across_pages():
    page1 = parse_workflow_page(_read("list_page_1.html"), GARAGE)
    page2 = parse_workflow_page(_read("list_page_2.html"), GARAGE)
    merged = merge_workflow_snapshots(GARAGE, [page1, page2])
    assert merged.pages_seen == 2
    ids = [row.record_id for row in merged.rows]
    assert ids.count("5f1a2b3c4d5e6f7a8b9c0d1f") == 1
    assert merged.rows_seen == 3


def test_all_five_detail_dates_are_read_and_parse():
    values = parse_detail_fields(_read("detail_full.html"), SHARED_DETAIL_FIELDS)
    creation, _ = parse_french_date(values["date_creation"], "Africa/Casablanca")
    assert creation is not None
    assert creation.strftime("%d/%m/%Y %H:%M") == "01/02/2026 09:15"
    for key in ("date_premiere_fin_prevue", "date_fin_travaux_prevue", "date_photos_avant"):
        assert parse_french_date(values[key], "Africa/Casablanca")[0] is not None
    quote, _ = parse_french_date(values["date_envoi_devis_garage"], "Africa/Casablanca")
    assert quote is not None and quote.strftime("%d/%m/%Y %H:%M") == "03/02/2026 11:00"


def test_invalid_or_empty_date_keeps_raw_text_and_parses_to_none():
    values = parse_detail_fields(_read("detail_partial.html"), SHARED_DETAIL_FIELDS)
    parsed, raw = parse_french_date(values["date_premiere_fin_prevue"], "Africa/Casablanca")
    assert parsed is None and raw == "-"
    parsed, raw = parse_french_date(values["date_fin_travaux_prevue"], "Africa/Casablanca")
    assert parsed is None and raw == ""
    parsed, raw = parse_french_date(values["date_envoi_devis_garage"], "Africa/Casablanca")
    assert parsed is None and raw == "pas une date"


@pytest.mark.parametrize(
    ("raw", "expect_none"),
    [("", True), ("-", True), ("—", True), ("n'importe quoi", True), ("31/02/2026", True)],
)
def test_parse_french_date_handles_invalid_input(raw: str, expect_none: bool):
    value, kept_raw = parse_french_date(raw, "Africa/Casablanca")
    assert (value is None) is expect_none
    assert kept_raw == raw.strip()


def test_detect_auth_required_true_for_login_form():
    assert detect_auth_required(_read("login_page.html")) is True


def test_detect_auth_required_true_for_session_validation_screen():
    assert detect_auth_required(_read("session_validation.html")) is True


def test_detect_auth_required_false_for_authenticated_page():
    assert detect_auth_required(_read("authenticated_page.html")) is False
