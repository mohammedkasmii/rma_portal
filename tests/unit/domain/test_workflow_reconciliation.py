"""The notification policy, exactly as documented in the V2 plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rma_portal.domain.enums import NotificationClass, NotificationKind, WorkflowEventKind
from rma_portal.domain.workflow_reconciliation import (
    Decision,
    counts_as_actionable_new,
    decide_change,
    decide_departure,
    decide_first_appearance,
    decide_return,
    detect_material_change,
    fingerprint_fields,
    fingerprint_value,
    needs_detail_read,
)
from rma_portal.infrastructure.portal.workflow_catalog import default_catalog

ACTION = NotificationClass.ACTION
INFO = NotificationClass.INFORMATIONAL
SILENT = NotificationClass.SILENT

MATERIAL = frozenset({"portal_status", "observation_count", "report"})
ALERT = frozenset({"portal_status", "report"})


class TestFirstAppearance:
    def test_baseline_never_alerts_whatever_the_class(self):
        for klass in NotificationClass:
            assert decide_first_appearance(klass, alerts_enabled=False) == Decision()

    def test_action_workflow_creates_new_event_and_alert(self):
        assert decide_first_appearance(ACTION, alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_NEW, NotificationKind.WORKFLOW_ITEM_NEW
        )

    def test_informational_workflow_is_a_completed_activity_without_alert(self):
        assert decide_first_appearance(INFO, alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_COMPLETED, None
        )

    def test_silent_workflow_records_nothing(self):
        assert decide_first_appearance(SILENT, alerts_enabled=True) == Decision()


class TestReturn:
    def test_action_return_alerts_again(self):
        assert decide_return(ACTION, alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_RETURNED, NotificationKind.WORKFLOW_ITEM_RETURNED
        )

    def test_informational_return_is_activity_only(self):
        assert decide_return(INFO, alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_RETURNED, None
        )

    def test_return_before_baseline_or_when_silent_records_nothing(self):
        assert decide_return(ACTION, alerts_enabled=False) == Decision()
        assert decide_return(SILENT, alerts_enabled=True) == Decision()


class TestMaterialChange:
    def test_no_difference_is_not_a_change(self):
        before = {"portal_status": "En cours", "observation_count": "0"}
        assert (
            detect_material_change(
                before, dict(before), material_fields=MATERIAL, alert_fields=ALERT
            )
            is None
        )

    def test_whitespace_only_differences_are_ignored(self):
        change = detect_material_change(
            {"portal_status": "En  cours"},
            {"portal_status": "En cours "},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is None

    def test_status_change_is_material_and_alerting(self):
        change = detect_material_change(
            {"portal_status": "En cours"},
            {"portal_status": "Accord reçu"},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is not None
        assert change.changed_fields == ("portal_status",)
        assert change.alerting is True

    def test_context_only_change_is_material_but_not_alerting(self):
        change = detect_material_change(
            {"observation_count": "0"},
            {"observation_count": "2"},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is not None and change.alerting is False

    def test_identity_fields_are_never_material(self):
        change = detect_material_change(
            {"insured_name": "A"},
            {"insured_name": "B"},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is None

    def test_a_field_never_stored_before_sets_its_own_baseline(self):
        change = detect_material_change(
            {"portal_status": "En cours"},
            {"portal_status": "En cours", "report": "Disponible"},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is None

    def test_a_field_that_disappears_counts_as_a_change_to_blank(self):
        change = detect_material_change(
            {"report": "Disponible"}, {}, material_fields=MATERIAL, alert_fields=ALERT
        )
        assert change is not None and change.changed_fields == ("report",)

    def test_fingerprints_hide_the_customer_value(self):
        change = detect_material_change(
            {"portal_status": "Statut confidentiel A"},
            {"portal_status": "Statut confidentiel B"},
            material_fields=MATERIAL,
            alert_fields=ALERT,
        )
        assert change is not None
        for fingerprint in (*change.before.values(), *change.after.values()):
            assert "confidentiel" not in fingerprint
            assert len(fingerprint) == 12
        assert change.before != change.after
        assert change.before["portal_status"] == fingerprint_value("portal_status", "Statut confidentiel A")


class TestChangeDecision:
    def _change(self, *, alerting: bool):
        key = "portal_status" if alerting else "observation_count"
        return detect_material_change(
            {key: "a"}, {key: "b"}, material_fields=MATERIAL, alert_fields=ALERT
        )

    def test_action_workflow_alerts_only_for_alerting_fields(self):
        assert decide_change(ACTION, self._change(alerting=True), alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED, NotificationKind.WORKFLOW_ITEM_CHANGED
        )
        assert decide_change(ACTION, self._change(alerting=False), alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED, None
        )

    def test_informational_workflow_never_raises_an_alert(self):
        assert decide_change(INFO, self._change(alerting=True), alerts_enabled=True) == Decision(
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED, None
        )

    def test_silent_workflow_or_missing_change_or_baseline_records_nothing(self):
        change = self._change(alerting=True)
        assert decide_change(SILENT, change, alerts_enabled=True) == Decision()
        assert decide_change(ACTION, None, alerts_enabled=True) == Decision()
        assert decide_change(ACTION, change, alerts_enabled=False) == Decision()


def test_departure_is_activity_for_action_and_informational_only():
    assert decide_departure(ACTION, alerts_enabled=True) == Decision(WorkflowEventKind.WORKFLOW_ITEM_LEFT)
    assert decide_departure(INFO, alerts_enabled=True) == Decision(WorkflowEventKind.WORKFLOW_ITEM_LEFT)
    assert decide_departure(SILENT, alerts_enabled=True) == Decision()
    assert decide_departure(ACTION, alerts_enabled=False) == Decision()


def test_actionable_new_counter_counts_arrivals_but_not_changes():
    assert counts_as_actionable_new(NotificationKind.WORKFLOW_ITEM_NEW)
    assert counts_as_actionable_new(NotificationKind.WORKFLOW_ITEM_RETURNED)
    assert counts_as_actionable_new(NotificationKind.NEW_AGREEMENT_DOSSIER)
    assert not counts_as_actionable_new(NotificationKind.WORKFLOW_ITEM_CHANGED)


def test_row_fingerprint_is_order_insensitive_and_value_sensitive():
    assert fingerprint_fields({"a": "1", "b": "2"}) == fingerprint_fields({"b": "2", "a": "1"})
    assert fingerprint_fields({"a": "1"}) != fingerprint_fields({"a": "2"})
    assert len(fingerprint_fields({})) == 64


def test_no_policy_function_infers_overdue_or_sla_alerts():
    """No SLA is confirmed: the only notification kinds are arrival/return/change."""
    assert {kind.value for kind in NotificationKind} == {
        "NEW_AGREEMENT_DOSSIER",
        "WORKFLOW_ITEM_NEW",
        "WORKFLOW_ITEM_RETURNED",
        "WORKFLOW_ITEM_CHANGED",
    }
    assert not any("OVERDUE" in kind.value or "SLA" in kind.value for kind in WorkflowEventKind)


class TestNeedsDetailRead:
    NOW = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    catalog = default_catalog()

    def _needs(self, key: str, fields, last_attempt, **kw) -> bool:
        definition = self.catalog.get(key)
        return needs_detail_read(
            definition,
            fields,
            last_attempt,
            failed=kw.get("failed", False),
            forced=kw.get("forced", False),
            now=kw.get("now", self.NOW),
        )

    def test_workflows_without_required_detail_never_read_it(self):
        assert not self._needs("photos_pending", {}, None, forced=True)

    def test_new_or_never_attempted_dossier_is_read(self):
        assert self._needs("agreement_normal", {}, None)
        assert self._needs("agreement_normal", {"date_envoi_devis_garage": "x"}, self.NOW, forced=True)

    def test_populated_required_value_stops_further_reads(self):
        stale = self.NOW - timedelta(days=1)
        assert not self._needs("agreement_normal", {"date_envoi_devis_garage": "03/02/2026"}, stale)

    def test_garage_rereads_a_blank_quote_date_on_every_poll(self):
        just_now = self.NOW - timedelta(seconds=1)
        assert self._needs("agreement_garage", {"date_envoi_devis_garage": ""}, just_now)
        assert self._needs("agreement_garage", {}, just_now)

    def test_other_workflows_throttle_blank_rereads_to_their_refresh_interval(self):
        recent = self.NOW - timedelta(minutes=10)
        old = self.NOW - timedelta(hours=2)
        assert not self._needs("agreement_normal", {"date_envoi_devis_garage": ""}, recent)
        assert self._needs("agreement_normal", {"date_envoi_devis_garage": ""}, old)

    def test_failed_attempt_is_retried_after_the_interval(self):
        old = self.NOW - timedelta(hours=2)
        recent = self.NOW - timedelta(minutes=1)
        fields = {"date_envoi_devis_garage": "03/02/2026"}
        assert self._needs("agreement_normal", fields, old, failed=True)
        assert not self._needs("agreement_normal", fields, recent, failed=True)


@pytest.mark.parametrize("definition", default_catalog().definitions(), ids=lambda d: d.key)
def test_every_definition_yields_a_decision_for_every_observation(definition):
    """Smoke: no catalogued workflow class can crash the policy."""
    klass = definition.notification_class
    for decide in (decide_first_appearance, decide_return, decide_departure):
        assert isinstance(decide(klass, alerts_enabled=True), Decision)
