from __future__ import annotations

from pathlib import Path

import pytest

from rma_portal.infrastructure.portal.parser import (
    detect_auth_required,
    merge_snapshots,
    parse_dossier_details,
    parse_french_date,
    parse_page_count,
    parse_queue_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_every_list_field():
    snapshot = parse_queue_page(_read("list_page_1.html"))
    row = next(r for r in snapshot.rows if r.record_id == "5f1a2b3c4d5e6f7a8b9c0d1f")
    assert row.dossier_number == "DOS-0002"
    assert row.insured_name == "Bilal Exemple"
    assert row.procedure == "Garage agréé"
    assert row.registration == "6789-B-12"
    assert row.garage == "Garage Exemple Deux"
    assert row.estimate_amount_raw == "8 500,00 MAD"
    assert row.portal_status == "En cours"
    assert row.city == "Rabat"
    assert row.observation_count == "0"
    assert row.agreement_login == "login-deux"


def test_stable_row_id_is_the_tr_id_attribute():
    snapshot = parse_queue_page(_read("list_page_1.html"))
    ids = {row.record_id for row in snapshot.rows}
    assert "5f1a2b3c4d5e6f7a8b9c0d1e" in ids
    assert "5f1a2b3c4d5e6f7a8b9c0d1f" in ids


def test_exact_details_href_skips_the_decoy_observation_link():
    snapshot = parse_queue_page(_read("list_page_1.html"))
    row = next(r for r in snapshot.rows if r.record_id == "5f1a2b3c4d5e6f7a8b9c0d1e")
    assert row.details_href == (
        "#dossiers-en-instance-accord/view-dossier-details/5f1a2b3c4d5e6f7a8b9c0d1e?ref=list_1874"
    )
    assert "view-dossier-details7" not in row.details_href


def test_duplicate_id_removed_within_one_page():
    snapshot = parse_queue_page(_read("list_page_1.html"))
    ids = [row.record_id for row in snapshot.rows]
    assert ids.count("5f1a2b3c4d5e6f7a8b9c0d1e") == 1


def test_two_page_pagination_reports_two_pages():
    assert parse_page_count(_read("list_page_1.html")) == 2


def test_merge_snapshots_deduplicates_across_pages():
    page1 = parse_queue_page(_read("list_page_1.html"))
    page2 = parse_queue_page(_read("list_page_2.html"))
    merged = merge_snapshots([page1, page2])
    assert merged.pages_seen == 2
    ids = [row.record_id for row in merged.rows]
    assert ids.count("5f1a2b3c4d5e6f7a8b9c0d1f") == 1
    assert merged.rows_seen == 3


def test_all_five_detail_dates_are_parsed():
    dates = parse_dossier_details(_read("detail_full.html"), "Africa/Casablanca")
    assert dates.date_creation is not None
    assert dates.date_creation.strftime("%d/%m/%Y %H:%M") == "01/02/2026 09:15"
    assert dates.date_premiere_fin_prevue is not None
    assert dates.date_fin_travaux_prevue is not None
    assert dates.date_envoi_devis_garage is not None
    assert dates.date_envoi_devis_garage.strftime("%d/%m/%Y %H:%M") == "03/02/2026 11:00"
    assert dates.date_photos_avant is not None


def test_invalid_or_empty_date_keeps_raw_text_and_parses_to_none():
    dates = parse_dossier_details(_read("detail_partial.html"), "Africa/Casablanca")
    assert dates.date_premiere_fin_prevue is None
    assert dates.date_premiere_fin_prevue_raw == "-"
    assert dates.date_fin_travaux_prevue is None
    assert dates.date_fin_travaux_prevue_raw == ""
    assert dates.date_envoi_devis_garage is None
    assert dates.date_envoi_devis_garage_raw == "pas une date"


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
