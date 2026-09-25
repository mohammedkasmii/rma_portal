"""V2 repositories: occurrences, membership work, per-user reads, sync runs, outbox, AI audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rma_portal.domain.enums import (
    AiFeature,
    AiRunStatus,
    NotificationClass,
    NotificationKind,
    OccurrenceOrigin,
    PollStatus,
    Role,
    SyncTrigger,
    WorkflowEventKind,
    WorkflowRulesStatus,
    WorkStatus,
)
from rma_portal.domain.models import (
    AiRun,
    Dossier,
    DossierDates,
    User,
    Workflow,
    WorkflowEvent,
    WorkStatusConflict,
)

NOW = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


def _workflow(account_id: int, key: str, **overrides) -> Workflow:
    values = {
        "id": None,
        "portal_account_id": account_id,
        "key": key,
        "name": key,
        "category": "Test",
        "route": f"#{key}/",
        "view_id": "view_1",
        "enabled": True,
        "sort_order": 10,
        "rules_status": WorkflowRulesStatus.CAPTURE_DERIVED,
        "baseline_completed_at": None,
        "last_poll_at": None,
        "last_success_at": None,
        "last_error": None,
    }
    values.update(overrides)
    return Workflow(**values)


def _make_dossier(uow, account_id: int, record_id: str) -> int:
    from rma_portal.application.dto import QueueRow

    row = QueueRow(
        record_id=record_id,
        dossier_number="D-1",
        insured_name="Assuré Synthétique",
        procedure="Garage agréé",
        registration="1-A-1",
        garage="Garage Test",
        estimate_amount_raw="",
        portal_status="En cours",
        city="Casablanca",
        observation_count="0",
        agreement_login="",
        details_href=f"#x/view-dossier-details/{record_id}/",
    )
    return uow.dossiers.create_from_row(account_id, row, NOW).id


def _make_user(uow, username: str) -> int:
    return uow.users.create(
        User(
            id=None,
            username=username,
            display_name=username,
            password_hash="hash",
            role=Role.EMPLOYEE,
            active=True,
            created_at=NOW,
        )
    ).id


def _membership(uow, workflow_id: int, dossier_id: int):
    membership = uow.workflow_memberships.create(
        workflow_id=workflow_id,
        dossier_id=dossier_id,
        seen_at=NOW,
        captured_fields={"status": "A"},
        fingerprint="abc",
    )
    uow.workflow_work.create_initial(membership.id, NOW)
    return membership


def test_workflow_catalog_fields_round_trip_and_admin_config_is_separate(uow_factory, portal_account_id):
    with uow_factory() as uow:
        created = uow.workflows.create(
            _workflow(
                portal_account_id,
                "agreement_normal",
                notification_class=NotificationClass.ACTION,
                filter_field="field_219",
                filter_operator="is",
                filter_value="5eebcdf9c791c6001505bd70",
                filter_label="Procédure normale",
                primary_date_key="date_envoi_devis_garage",
            )
        )
        uow.commit()
    with uow_factory() as uow:
        loaded = uow.workflows.get_by_key(portal_account_id, "agreement_normal")
        assert loaded.rules_status is WorkflowRulesStatus.CAPTURE_DERIVED
        assert loaded.filter_value == "5eebcdf9c791c6001505bd70"

        # An administrator disables it and marks it informational...
        uow.workflows.update_admin_config(
            created.id, enabled=False, notification_class=NotificationClass.INFORMATIONAL
        )
        # ...and a later catalog refresh must not undo that decision.
        refreshed = _workflow(
            portal_account_id, "agreement_normal", id=created.id, name="Nouveau nom", catalog_version=2
        )
        uow.workflows.update_definition(refreshed)
        uow.commit()
    with uow_factory() as uow:
        loaded = uow.workflows.get(created.id)
        assert loaded.name == "Nouveau nom"
        assert loaded.catalog_version == 2
        assert loaded.enabled is False
        assert loaded.notification_class is NotificationClass.INFORMATIONAL


def test_reappearance_creates_a_new_immutable_occurrence(uow_factory, portal_account_id):
    with uow_factory() as uow:
        workflow = uow.workflows.create(_workflow(portal_account_id, "hifad_search"))
        dossier_id = _make_dossier(uow, portal_account_id, "rec-1")
        membership = _membership(uow, workflow.id, dossier_id)
        first = uow.workflow_occurrences.create(
            membership_id=membership.id,
            workflow_id=workflow.id,
            dossier_id=dossier_id,
            occurrence_number=1,
            origin=OccurrenceOrigin.NEW,
            detected_at=NOW,
        )
        uow.workflow_memberships.deactivate(membership.id)
        number = uow.workflow_memberships.reactivate(
            membership.id, seen_at=NOW + timedelta(hours=2), captured_fields={}, fingerprint="d"
        )
        second = uow.workflow_occurrences.create(
            membership_id=membership.id,
            workflow_id=workflow.id,
            dossier_id=dossier_id,
            occurrence_number=number,
            origin=OccurrenceOrigin.RETURNED,
            detected_at=NOW + timedelta(hours=2),
        )
        uow.commit()
        assert number == 2 and second.id != first.id
        history = uow.workflow_occurrences.list_for_membership(membership.id)
        assert [(o.occurrence_number, o.origin) for o in history] == [
            (1, OccurrenceOrigin.NEW),
            (2, OccurrenceOrigin.RETURNED),
        ]
        assert not hasattr(uow.workflow_occurrences, "update")


def test_work_status_belongs_to_the_membership(uow_factory, portal_account_id):
    with uow_factory() as uow:
        garage = uow.workflows.create(_workflow(portal_account_id, "agreement_garage"))
        photos = uow.workflows.create(_workflow(portal_account_id, "photos_pending"))
        user_id = _make_user(uow, "alice")
        dossier_id = _make_dossier(uow, portal_account_id, "rec-1")
        in_garage = _membership(uow, garage.id, dossier_id)
        in_photos = _membership(uow, photos.id, dossier_id)

        done = uow.workflow_work.upsert(in_garage.id, WorkStatus.DONE, 1, user_id, NOW)
        assert (done.status, done.version) == (WorkStatus.DONE, 2)
        # The same dossier stays TO_DO in the other queue.
        assert uow.workflow_work.get(in_photos.id).status is WorkStatus.TO_DO

        with pytest.raises(WorkStatusConflict):
            uow.workflow_work.upsert(in_garage.id, WorkStatus.WAITING, 1, user_id, NOW)
        counts = uow.workflow_work.counts_by_workflow([garage.id, photos.id])
        assert counts == {(garage.id, WorkStatus.DONE): 1, (photos.id, WorkStatus.TO_DO): 1}


def test_acknowledging_an_occurrence_is_per_employee_and_per_occurrence(uow_factory, portal_account_id):
    with uow_factory() as uow:
        workflow = uow.workflows.create(_workflow(portal_account_id, "estimate_pending"))
        alice = _make_user(uow, "alice")
        bob = _make_user(uow, "bob")
        dossier_id = _make_dossier(uow, portal_account_id, "rec-1")
        membership = _membership(uow, workflow.id, dossier_id)
        occurrences = []
        for number, origin in ((1, OccurrenceOrigin.NEW), (2, OccurrenceOrigin.RETURNED)):
            occurrence = uow.workflow_occurrences.create(
                membership_id=membership.id,
                workflow_id=workflow.id,
                dossier_id=dossier_id,
                occurrence_number=number,
                origin=origin,
                detected_at=NOW + timedelta(hours=number),
            )
            occurrences.append(occurrence)
            uow.notifications.create_for_occurrence(
                dossier_id=dossier_id,
                workflow_id=workflow.id,
                occurrence_id=occurrence.id,
                kind=(
                    NotificationKind.WORKFLOW_ITEM_NEW
                    if number == 1
                    else NotificationKind.WORKFLOW_ITEM_RETURNED
                ),
                detected_at=NOW,
            )
        first, second = occurrences

        assert uow.notifications.acknowledge_occurrence(first.id, alice, NOW) == 1
        assert uow.notifications.acknowledge_occurrence(first.id, alice, NOW) == 0  # idempotent

        assert not uow.notifications.is_occurrence_unread_for_user(first.id, alice)
        assert uow.notifications.is_occurrence_unread_for_user(first.id, bob)
        assert uow.notifications.is_occurrence_unread_for_user(second.id, alice)
        assert uow.notifications.unread_counts_for_user(alice) == {
            (workflow.id, NotificationKind.WORKFLOW_ITEM_RETURNED): 1
        }
        assert uow.notifications.unread_counts_for_user(bob) == {
            (workflow.id, NotificationKind.WORKFLOW_ITEM_NEW): 1,
            (workflow.id, NotificationKind.WORKFLOW_ITEM_RETURNED): 1,
        }

        # A new employee starts with no inherited unread alerts.
        carol = _make_user(uow, "carol")
        uow.notifications.mark_all_existing_as_read_for_user(carol, NOW)
        assert uow.notifications.unread_counts_for_user(carol) == {}


def test_sync_run_aggregates_independent_workflow_outcomes(uow_factory, portal_account_id):
    with uow_factory() as uow:
        good = uow.workflows.create(_workflow(portal_account_id, "good"))
        bad = uow.workflows.create(_workflow(portal_account_id, "bad"))
        run = uow.sync_runs.start(portal_account_id, SyncTrigger.MANUAL, NOW)
        ok = uow.workflow_poll_runs.start(run.id, good.id, NOW, baseline=True)
        ko = uow.workflow_poll_runs.start(run.id, bad.id, NOW, baseline=False)
        for poll, status, error in ((ok, PollStatus.COMPLETE, None), (ko, PollStatus.FAILED, "boom")):
            uow.workflow_poll_runs.finish(
                poll.id,
                status=status,
                completed_at=NOW,
                rows_seen=3,
                pages_seen=1,
                details_failed=0,
                created_count=3,
                returned_count=0,
                changed_count=0,
                left_count=0,
                notifications_created=0,
                error=error,
            )
        finished = uow.sync_runs.finish(
            run.id,
            status=PollStatus.PARTIAL,
            completed_at=NOW,
            workflows_total=2,
            workflows_complete=1,
            workflows_partial=0,
            workflows_auth_required=0,
            workflows_failed=1,
            error=None,
        )
        uow.commit()
        assert finished.status is PollStatus.PARTIAL
        outcomes = {
            p.workflow_id: p.status for p in uow.workflow_poll_runs.list_for_sync_run(run.id)
        }
        assert outcomes == {good.id: PollStatus.COMPLETE, bad.id: PollStatus.FAILED}
        assert uow.sync_runs.latest(portal_account_id).id == run.id


def test_events_store_field_names_and_fingerprints_only(uow_factory, portal_account_id):
    with uow_factory() as uow:
        workflow = uow.workflows.create(_workflow(portal_account_id, "reformed"))
        dossier_id = _make_dossier(uow, portal_account_id, "rec-1")
        stored = uow.workflow_events.add(
            WorkflowEvent(
                id=None,
                kind=WorkflowEventKind.WORKFLOW_ITEM_CHANGED,
                detected_at=NOW,
                notification_class=NotificationClass.ACTION,
                workflow_id=workflow.id,
                dossier_id=dossier_id,
                changed_fields=("portal_status",),
                before_fingerprints={"portal_status": "1a2b3c4d"},
                after_fingerprints={"portal_status": "5e6f7a8b"},
            )
        )
        uow.workflow_events.add(
            WorkflowEvent(
                id=None,
                kind=WorkflowEventKind.SESSION_AUTH_REQUIRED,
                detected_at=NOW,
                notification_class=NotificationClass.ACTION,
                message="Session OmegaFlow expirée",
            )
        )
        uow.commit()
        assert stored.changed_fields == ("portal_status",)
        assert [e.kind for e in uow.workflow_events.list_for_dossier(dossier_id)] == [
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED
        ]
        # Operational alerts are kept out of the employee feed.
        assert [e.kind for e in uow.workflow_events.list_recent()] == [
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED
        ]
        assert [e.kind for e in uow.workflow_events.list_recent(only_operational=True)] == [
            WorkflowEventKind.SESSION_AUTH_REQUIRED
        ]


def test_outbox_delivers_in_order_and_backs_off_failures(uow_factory):
    with uow_factory() as uow:
        first = uow.outbox.add("notification.created", {"notification_id": 1}, NOW)
        second = uow.outbox.add("notification.created", {"notification_id": 2}, NOW)
        uow.commit()
    with uow_factory() as uow:
        claimed = uow.outbox.claim_pending(NOW)
        assert [m.id for m in claimed] == [first.id, second.id]
        uow.outbox.mark_processed(first.id, NOW)
        uow.outbox.mark_failed(second.id, "handler exploded", NOW)
        uow.commit()
    with uow_factory() as uow:
        assert uow.outbox.claim_pending(NOW) == []  # backing off, not lost
        retried = uow.outbox.claim_pending(NOW + timedelta(hours=2))
        assert [m.id for m in retried] == [second.id]
        assert retried[0].attempts == 1 and retried[0].last_error == "handler exploded"
        assert uow.outbox.pending_count() == 1


def test_ai_run_audit_keeps_context_hash_not_context(uow_factory):
    with uow_factory() as uow:
        stored = uow.ai_runs.add(
            AiRun(
                id=None,
                feature=AiFeature.DOSSIER_SUMMARY,
                subject_type="dossier",
                subject_id=7,
                model="qwen3:8b",
                prompt_version="v1",
                context_hash="a" * 64,
                status=AiRunStatus.SUCCEEDED,
                started_at=NOW,
                duration_ms=812,
                result={"summary": "Synthèse test"},
            )
        )
        uow.commit()
        latest = uow.ai_runs.latest_successful(AiFeature.DOSSIER_SUMMARY, "dossier", 7)
        assert latest.id == stored.id
        assert latest.result == {"summary": "Synthèse test"}
        assert uow.ai_runs.latest_successful(AiFeature.DOSSIER_SUMMARY, "dossier", 8) is None


def test_dossier_carries_detail_fields_default(uow_factory, portal_account_id):
    with uow_factory() as uow:
        dossier_id = _make_dossier(uow, portal_account_id, "rec-1")
        dossier: Dossier = uow.dossiers.get(dossier_id)
        assert dossier.detail_fields == {}
        assert dossier.dates == DossierDates()
