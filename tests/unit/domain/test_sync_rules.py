from __future__ import annotations

from rma_portal.domain.enums import PollStatus
from rma_portal.domain.sync_rules import ExistingDossierState, ReconciliationInput, reconcile


def _existing(**overrides) -> dict[str, ExistingDossierState]:
    base = {"record_id": "aaa", "active": True, "missing_complete_polls": 0}
    base.update(overrides)
    return {base["record_id"]: ExistingDossierState(**base)}


def test_first_complete_poll_creates_baseline_with_no_notifications():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=False,
            seen_record_ids=frozenset({"a", "b"}),
            existing={},
        )
    )
    assert result.to_create == {"a", "b"}
    assert result.create_notifications is False
    assert result.is_baseline_poll is True
    assert result.to_reactivate == frozenset()
    assert result.absence_increments == {}


def test_unseen_id_after_baseline_creates_and_notifies():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=True,
            seen_record_ids=frozenset({"new-1"}),
            existing={},
        )
    )
    assert result.to_create == {"new-1"}
    assert result.create_notifications is True
    assert result.is_baseline_poll is False


def test_active_known_id_seen_again_is_only_touched():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=True,
            seen_record_ids=frozenset({"a"}),
            existing=_existing(record_id="a", active=True),
        )
    )
    assert result.to_touch == {"a"}
    assert result.to_create == frozenset()
    assert result.to_reactivate == frozenset()


def test_inactive_id_reappearing_is_reactivated_and_notified():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=True,
            seen_record_ids=frozenset({"a"}),
            existing=_existing(record_id="a", active=False, missing_complete_polls=2),
        )
    )
    assert result.to_reactivate == {"a"}
    assert result.create_notifications is True


def test_one_missing_complete_poll_keeps_dossier_active():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=True,
            seen_record_ids=frozenset(),
            existing=_existing(record_id="a", active=True, missing_complete_polls=0),
        )
    )
    assert result.absence_increments == {"a": 1}
    assert result.to_deactivate == frozenset()


def test_two_missing_complete_polls_deactivate():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.COMPLETE,
            baseline_already_completed=True,
            seen_record_ids=frozenset(),
            existing=_existing(record_id="a", active=True, missing_complete_polls=1),
        )
    )
    assert result.absence_increments == {"a": 2}
    assert result.to_deactivate == {"a"}


def test_partial_poll_never_touches_absence_counters():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.PARTIAL,
            baseline_already_completed=True,
            seen_record_ids=frozenset(),
            existing=_existing(record_id="a", active=True, missing_complete_polls=1),
        )
    )
    assert result.absence_increments == {}
    assert result.to_deactivate == frozenset()


def test_partial_poll_still_detects_new_and_touched_rows():
    result = reconcile(
        ReconciliationInput(
            poll_status=PollStatus.PARTIAL,
            baseline_already_completed=True,
            seen_record_ids=frozenset({"a", "new"}),
            existing=_existing(record_id="a", active=True),
        )
    )
    assert result.to_touch == {"a"}
    assert result.to_create == {"new"}


def test_auth_required_and_failed_polls_do_nothing():
    for status in (PollStatus.AUTH_REQUIRED, PollStatus.FAILED):
        result = reconcile(
            ReconciliationInput(
                poll_status=status,
                baseline_already_completed=True,
                seen_record_ids=frozenset(),
                existing=_existing(record_id="a", active=True, missing_complete_polls=1),
            )
        )
        assert result.reconciled is False
        assert result.to_create == frozenset()
        assert result.absence_increments == {}
        assert result.to_deactivate == frozenset()
