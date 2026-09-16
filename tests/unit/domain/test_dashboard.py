from __future__ import annotations

from datetime import UTC, datetime

from rma_portal.application.dashboard import compute_counts, filter_and_sort
from rma_portal.application.dto import DashboardRow
from rma_portal.domain.enums import WorkStatus


def _row(**overrides) -> DashboardRow:
    base = {
        "dossier_id": 1,
        "dossier_number": "D-1",
        "insured_name": "Client Test",
        "registration": "AA-000-AA",
        "garage": "Garage Test",
        "portal_status": "En cours",
        "date_envoi_devis_garage": datetime(2026, 1, 1, tzinfo=UTC),
        "date_envoi_devis_garage_raw": "01/01/2026",
        "date_fin_travaux_prevue": None,
        "date_fin_travaux_prevue_raw": "",
        "detection_time": datetime(2026, 1, 1, tzinfo=UTC),
        "work_status": WorkStatus.TO_DO,
        "unread": False,
    }
    base.update(overrides)
    return DashboardRow(**base)


def test_unread_rows_sort_first():
    rows = [
        _row(dossier_id=1, unread=False, date_envoi_devis_garage=datetime(2026, 6, 1, tzinfo=UTC)),
        _row(dossier_id=2, unread=True, date_envoi_devis_garage=datetime(2026, 1, 1, tzinfo=UTC)),
    ]
    result = filter_and_sort(rows)
    assert [r.dossier_id for r in result] == [2, 1]


def test_sort_by_date_envoi_devis_garage_descending_within_unread_group():
    rows = [
        _row(dossier_id=1, unread=True, date_envoi_devis_garage=datetime(2026, 1, 1, tzinfo=UTC)),
        _row(dossier_id=2, unread=True, date_envoi_devis_garage=datetime(2026, 6, 1, tzinfo=UTC)),
    ]
    result = filter_and_sort(rows)
    assert [r.dossier_id for r in result] == [2, 1]


def test_search_matches_number_name_registration_garage():
    rows = [
        _row(dossier_id=1, dossier_number="D-123", insured_name="Ali", registration="1", garage="X"),
        _row(dossier_id=2, dossier_number="D-999", insured_name="Sami", registration="2", garage="Y"),
    ]
    assert [r.dossier_id for r in filter_and_sort(rows, search="d-123")] == [1]
    assert [r.dossier_id for r in filter_and_sort(rows, search="sami")] == [2]


def test_unread_only_filter():
    rows = [_row(dossier_id=1, unread=True), _row(dossier_id=2, unread=False)]
    assert [r.dossier_id for r in filter_and_sort(rows, unread_only=True)] == [1]


def test_work_status_filter():
    rows = [
        _row(dossier_id=1, work_status=WorkStatus.DONE),
        _row(dossier_id=2, work_status=WorkStatus.TO_DO),
    ]
    assert [r.dossier_id for r in filter_and_sort(rows, work_status=WorkStatus.DONE)] == [1]


def test_compute_counts():
    rows = [
        _row(dossier_id=1, unread=True, work_status=WorkStatus.TO_DO),
        _row(dossier_id=2, unread=False, work_status=WorkStatus.IN_PROGRESS),
        _row(dossier_id=3, unread=False, work_status=WorkStatus.WAITING),
        _row(dossier_id=4, unread=False, work_status=WorkStatus.DONE),
    ]
    counts = compute_counts(rows)
    assert counts.new_for_me == 1
    assert counts.to_do == 1
    assert counts.in_progress == 1
    assert counts.waiting == 1
    assert counts.done == 1
