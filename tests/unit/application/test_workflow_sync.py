"""Multi-workflow synchronization: baselines, alerts, memberships, isolation and cleanup."""

from __future__ import annotations

import asyncio
import gc
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from rma_portal.application import workflow_sync as workflow_sync_module
from rma_portal.application.accounts import AccountService
from rma_portal.application.dto import DetailReadError, PortalReadError
from rma_portal.domain.enums import (
    NotificationClass,
    NotificationKind,
    PollStatus,
    Role,
    SessionStatus,
    SyncTrigger,
    WorkflowEventKind,
    WorkflowRulesStatus,
    WorkStatus,
)
from tests.support import (
    FakeCycleLock,
    StepClock,
    make_sync,
    seed_member,
    seed_workflows,
    set_enabled,
    set_rules,
)
from tests.unit.application.fakes import (
    FakePortalReaderFactory,
    auth_required,
    complete,
    failed,
    partial,
    row,
)

GARAGE = "agreement_garage"
PHOTOS = "photos_pending"
NORMAL = "agreement_normal"
VALIDATED = "agreement_validated"  # INFORMATIONAL
FIRST_EXPERT = "collegial_first_expert"
FIRST_AGREED = "collegial_first_agreed"


class _Hasher:
    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hash:{password}"


def _users(uow_factory, *names: str) -> dict[str, int]:
    service = AccountService(uow_factory, _Hasher())
    return {
        name: service.create_user(
            username=name, display_name=name.title(), password="password123", role=Role.EMPLOYEE
        ).id
        for name in names
    }


def _scalar(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def _memberships(engine, key: str) -> dict[str, tuple[bool, int, int]]:
    """record_id -> (active, missing_complete_polls, occurrence_number)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select d.record_id, m.active, m.missing_complete_polls, m.occurrence_number "
                "from workflow_memberships m join workflows w on w.id = m.workflow_id "
                "join dossiers d on d.id = m.dossier_id where w.key = :key"
            ),
            {"key": key},
        ).all()
    return {r.record_id: (bool(r.active), r.missing_complete_polls, r.occurrence_number) for r in rows}


def _notification_kinds(engine, key: str | None = None) -> list[str]:
    sql = "select n.kind from notifications n join workflows w on w.id = n.workflow_id"
    params = {}
    if key:
        sql += " where w.key = :key"
        params["key"] = key
    with engine.connect() as conn:
        return sorted(conn.execute(text(sql), params).scalars())


def _event_kinds(engine, key: str | None = None) -> list[str]:
    sql = "select e.kind from workflow_events e left join workflows w on w.id = e.workflow_id"
    params = {}
    if key:
        sql += " where w.key = :key"
        params["key"] = key
    with engine.connect() as conn:
        return sorted(conn.execute(text(sql), params).scalars())


def _baseline_done(engine, key: str) -> bool:
    return (
        _scalar(engine, "select baseline_completed_at from workflows where key = :k", k=key)
        is not None
    )


@pytest.fixture
def clock() -> StepClock:
    return StepClock()


# --- baselines -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_workflow_establishes_its_own_silent_baseline(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    _users(uow_factory, "alice")
    factory = FakePortalReaderFactory(
        [
            {GARAGE: complete(GARAGE, row("g1"), row("g2")), PHOTOS: complete(PHOTOS, row("p1"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await sync.execute()

    assert result.status is PollStatus.COMPLETE
    assert result.notifications_created == 0
    assert _notification_kinds(engine) == []
    assert _baseline_done(engine, GARAGE) and _baseline_done(engine, PHOTOS)
    assert {w.baseline for w in result.workflows} == {True}
    # Baseline dossiers still get an immutable BASELINE occurrence and a TO_DO status.
    assert _scalar(engine, "select count(*) from workflow_occurrences where origin = 'BASELINE'") == 3
    assert _scalar(engine, "select count(*) from workflow_work where status = 'TO_DO'") == 3
    assert _event_kinds(engine) == []


@pytest.mark.asyncio
async def test_enabling_a_workflow_later_never_alerts_for_what_it_already_contained(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    _users(uow_factory, "alice")
    cycle1 = {GARAGE: complete(GARAGE, row("g1")), PHOTOS: complete(PHOTOS, *[row(f"p{i}") for i in range(5)])}
    cycle2 = {
        GARAGE: complete(GARAGE, row("g1"), row("g2")),  # a genuinely new garage dossier
        PHOTOS: complete(PHOTOS, *[row(f"p{i}") for i in range(5)]),
    }
    factory = FakePortalReaderFactory([cycle1, cycle2])
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    assert not _baseline_done(engine, PHOTOS)  # disabled: never even read
    assert factory.readers[0].read_keys == [GARAGE]

    set_enabled(uow_factory, PHOTOS, True)
    result = await sync.execute()

    photos = next(w for w in result.workflows if w.workflow_key == PHOTOS)
    garage = next(w for w in result.workflows if w.workflow_key == GARAGE)
    assert photos.baseline is True and photos.notifications_created == 0
    assert garage.notifications_created == 1
    assert _notification_kinds(engine, PHOTOS) == []
    assert _notification_kinds(engine, GARAGE) == ["WORKFLOW_ITEM_NEW"]


@pytest.mark.asyncio
async def test_an_empty_captured_queue_completes_the_baseline_and_later_arrivals_alert(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[FIRST_AGREED])
    factory = FakePortalReaderFactory(
        [{FIRST_AGREED: complete(FIRST_AGREED)}, {FIRST_AGREED: complete(FIRST_AGREED, row("x1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    first = await sync.execute()
    second = await sync.execute()

    assert first.status is PollStatus.COMPLETE and first.workflows[0].rows_seen == 0
    assert first.workflows[0].baseline is True
    assert second.notifications_created == 1
    assert _notification_kinds(engine) == ["WORKFLOW_ITEM_NEW"]


@pytest.mark.asyncio
async def test_a_partial_first_poll_creates_dossiers_silently_but_does_not_complete_the_baseline(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: partial(PHOTOS, row("p1"))},
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"), row("p3"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    first = await sync.execute()
    assert first.status is PollStatus.PARTIAL and first.notifications_created == 0
    assert not _baseline_done(engine, PHOTOS)

    second = await sync.execute()  # first COMPLETE poll: baseline, p2 is silent too
    assert second.notifications_created == 0 and _baseline_done(engine, PHOTOS)

    third = await sync.execute()
    assert third.notifications_created == 1


# --- new / returned ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_item_after_baseline_is_unread_for_every_existing_employee(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    users = _users(uow_factory, "alice", "bob")
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS, row("p1"))}, {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    result = await sync.execute()

    assert result.created == 1 and result.notifications_created == 1
    with uow_factory() as uow:
        for user_id in users.values():
            counts = uow.notifications.unread_counts_for_user(user_id)
            assert sum(counts.values()) == 1
            assert list(counts)[0][1] is NotificationKind.WORKFLOW_ITEM_NEW
    # The alert names its workflow, occurrence and dossier; the event says why.
    row_ = _scalar(
        engine,
        "select count(*) from notifications n join workflow_occurrences o on "
        "o.id = n.workflow_occurrence_id and o.origin = 'NEW' "
        "join workflow_events e on e.id = n.workflow_event_id and e.kind = 'WORKFLOW_ITEM_NEW' "
        "where n.workflow_id is not null and n.dossier_id = o.dossier_id",
    )
    assert row_ == 1


@pytest.mark.asyncio
async def test_one_complete_omission_keeps_the_membership_active(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
            {PHOTOS: complete(PHOTOS, row("p1"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    result = await sync.execute()

    assert result.deactivated == 0
    assert _memberships(engine, PHOTOS)["p2"] == (True, 1, 1)


@pytest.mark.asyncio
async def test_reappearance_after_two_complete_omissions_starts_a_new_occurrence_and_alert(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    alice = _users(uow_factory, "alice")["alice"]
    gone = complete(PHOTOS, row("p1"))
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},  # baseline
            {PHOTOS: gone},  # omission 1
            {PHOTOS: gone},  # omission 2 -> inactive
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},  # returns
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    await sync.execute()
    left = await sync.execute()
    assert left.deactivated == 1
    assert _memberships(engine, PHOTOS)["p2"] == (False, 2, 1)
    assert WorkflowEventKind.WORKFLOW_ITEM_LEFT.value in _event_kinds(engine, PHOTOS)

    back = await sync.execute()

    assert back.reactivated == 1 and back.notifications_created == 1
    assert _memberships(engine, PHOTOS)["p2"] == (True, 0, 2)
    assert _notification_kinds(engine) == ["WORKFLOW_ITEM_RETURNED"]
    # The first occurrence is untouched history; the new one is a distinct row.
    origins = _scalar(
        engine,
        "select group_concat(origin) from (select o.origin from workflow_occurrences o "
        "join dossiers d on d.id = o.dossier_id where d.record_id = 'p2' order by o.occurrence_number)",
    )
    assert origins == "BASELINE,RETURNED"
    with uow_factory() as uow:
        assert sum(uow.notifications.unread_counts_for_user(alice).values()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "silent_read",
    [
        lambda: partial(PHOTOS, row("p1")),
        lambda: failed(PHOTOS),
        lambda: auth_required(PHOTOS),
    ],
    ids=["partial", "failed", "auth_required"],
)
async def test_incomplete_polls_never_mark_a_record_absent(
    silent_read, uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
            {PHOTOS: silent_read()},
            {PHOTOS: silent_read()},
            {PHOTOS: silent_read()},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    for _ in range(4):
        await sync.execute()

    assert _memberships(engine, PHOTOS) == {"p1": (True, 0, 1), "p2": (True, 0, 1)}


@pytest.mark.asyncio
async def test_a_partial_read_still_creates_the_new_items_it_did_see(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    _users(uow_factory, "alice")
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1"))},
            {PHOTOS: partial(PHOTOS, row("p1"), row("p9"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    result = await sync.execute()

    assert result.status is PollStatus.PARTIAL
    assert result.notifications_created == 1
    assert "p9" in _memberships(engine, PHOTOS)


# --- per-user reads ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledgement_is_per_employee_and_a_new_employee_inherits_nothing(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    users = _users(uow_factory, "alice", "bob")
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS)}, {PHOTOS: complete(PHOTOS, row("p1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()
    await sync.execute()
    occurrence_id = _scalar(engine, "select id from workflow_occurrences")

    with uow_factory() as uow:
        assert uow.notifications.acknowledge_occurrence(occurrence_id, users["alice"], clock()) == 1
        uow.commit()
    late = _users(uow_factory, "carol")["carol"]

    with uow_factory() as uow:
        assert not uow.notifications.is_occurrence_unread_for_user(occurrence_id, users["alice"])
        assert uow.notifications.is_occurrence_unread_for_user(occurrence_id, users["bob"])
        assert uow.notifications.unread_counts_for_user(late) == {}


# --- multi-membership identity ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_dossier_in_two_workflows_has_independent_lifecycles_and_work_status(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    shared = row("shared", dossier_number="D-77", garage="Garage A")
    only_in_photos = complete(PHOTOS, row("shared", dossier_number="D-77"))
    factory = FakePortalReaderFactory(
        [
            {GARAGE: complete(GARAGE, shared), PHOTOS: complete(PHOTOS, shared)},
            {GARAGE: complete(GARAGE), PHOTOS: only_in_photos},
            {GARAGE: complete(GARAGE), PHOTOS: only_in_photos},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    # One shared dossier row, two memberships, two occurrences, two independent work rows.
    assert _scalar(engine, "select count(*) from dossiers") == 1
    assert _scalar(engine, "select count(*) from workflow_memberships") == 2
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        garage = uow.workflows.get_by_key(account.id, GARAGE)
        photos = uow.workflows.get_by_key(account.id, PHOTOS)
        dossier = uow.dossiers.get_by_record_id(account.id, "shared")
        in_garage = uow.workflow_memberships.get(garage.id, dossier.id)
        in_photos = uow.workflow_memberships.get(photos.id, dossier.id)
        uow.workflow_work.upsert(in_garage.id, WorkStatus.DONE, 1, _first_user(uow), clock())
        uow.commit()
    with uow_factory() as uow:
        assert uow.workflow_work.get(in_garage.id).status is WorkStatus.DONE
        assert uow.workflow_work.get(in_photos.id).status is WorkStatus.TO_DO

    await sync.execute()
    await sync.execute()  # second complete omission from garage only

    assert _memberships(engine, GARAGE)["shared"][0] is False
    assert _memberships(engine, PHOTOS)["shared"][0] is True
    # Still active in another queue, so the shared dossier itself stays active.
    assert _scalar(engine, "select active from dossiers") == 1


def _first_user(uow) -> int:
    users = uow.users.list_all()
    if users:
        return users[0].id
    from rma_portal.domain.models import User

    return uow.users.create(
        User(None, "seed", "Seed", "h", Role.EMPLOYEE, True, datetime.now(UTC))
    ).id


@pytest.mark.asyncio
async def test_dossier_becomes_inactive_only_when_it_left_every_workflow(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    shared = row("shared")
    gone = {GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)}
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE, shared), PHOTOS: complete(PHOTOS, shared)}, gone, gone]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    for _ in range(3):
        await sync.execute()

    assert _scalar(engine, "select active from dossiers") == 0


# --- material changes -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerting_change_in_an_action_workflow_creates_a_changed_badge_and_event(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    alice = _users(uow_factory, "alice")["alice"]
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1", portal_status="Instance photos", date_envoi="01/09/2026"))},
            {PHOTOS: complete(PHOTOS, row("p1", portal_status="Instance photos", date_envoi="05/09/2026"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    assert result.changed == 1 and result.notifications_created == 1
    assert _notification_kinds(engine) == ["WORKFLOW_ITEM_CHANGED"]
    assert _event_kinds(engine, PHOTOS) == ["WORKFLOW_ITEM_CHANGED"]
    with uow_factory() as uow:
        (event,) = uow.workflow_events.list_recent()
        assert event.changed_fields == ("date_envoi",)
        # Field names and short fingerprints only -- never the raw customer values.
        assert "05/09/2026" not in str(event.after_fingerprints)
        assert "01/09/2026" not in str(event.before_fingerprints)
        assert sum(uow.notifications.unread_counts_for_user(alice).values()) == 1


@pytest.mark.asyncio
async def test_non_alerting_change_is_an_activity_event_without_unread_badge(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [
            {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
            {PHOTOS: complete(PHOTOS, row("p1", observation_count="3", dossier_number="D-renamed"), row("p2"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    # observation_count is context (material, not alerting); the number is identity (ignored).
    assert result.changed == 0
    assert _event_kinds(engine, PHOTOS) == []


@pytest.mark.asyncio
async def test_context_field_change_is_recorded_as_activity_when_it_is_material(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[VALIDATED])
    factory = FakePortalReaderFactory(
        [
            {VALIDATED: complete(VALIDATED, row("v1", observation_count="0"))},
            {VALIDATED: complete(VALIDATED, row("v1", observation_count="2"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    assert result.changed == 1 and result.notifications_created == 0
    assert _event_kinds(engine, VALIDATED) == ["WORKFLOW_ITEM_CHANGED"]
    assert _notification_kinds(engine) == []


@pytest.mark.asyncio
async def test_change_in_an_informational_workflow_never_creates_an_unread_alert(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[VALIDATED])
    factory = FakePortalReaderFactory(
        [
            {VALIDATED: complete(VALIDATED, row("v1", agreement="En attente"))},
            {VALIDATED: complete(VALIDATED, row("v1", agreement="Accord joint"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    assert result.changed == 1 and result.notifications_created == 0
    assert _notification_kinds(engine) == []


@pytest.mark.asyncio
async def test_new_item_in_an_informational_workflow_is_a_completed_activity_not_an_alert(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[VALIDATED])
    factory = FakePortalReaderFactory(
        [{VALIDATED: complete(VALIDATED)}, {VALIDATED: complete(VALIDATED, row("v1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    assert result.notifications_created == 0
    assert _event_kinds(engine, VALIDATED) == ["WORKFLOW_ITEM_COMPLETED"]
    assert _notification_kinds(engine) == []


@pytest.mark.asyncio
async def test_silent_workflow_synchronizes_context_but_emits_nothing(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS], **{PHOTOS: NotificationClass.SILENT})
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS, row("p1"))}, {PHOTOS: complete(PHOTOS, row("p1"), row("p2", portal_status="x"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    await sync.execute()

    assert set(_memberships(engine, PHOTOS)) == {"p1", "p2"}
    assert _event_kinds(engine) == [] and _notification_kinds(engine) == []


@pytest.mark.asyncio
async def test_a_field_added_by_a_newer_catalog_never_fakes_a_change_on_first_sight(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    # A V1-era membership only remembers portal_status ("En cours") and the identity fields.
    seed_member(uow_factory, portal_account_id, GARAGE, "g1")
    with uow_factory() as uow:
        uow.workflows.mark_baseline_completed(
            uow.workflows.get_by_key(portal_account_id, GARAGE).id, clock()
        )
        uow.commit()
    factory = FakePortalReaderFactory(
        [
            # 'agreement' was never stored: it establishes its own baseline, no change.
            {GARAGE: complete(GARAGE, row("g1", agreement="Oui"))},
            # A stored field that really changed is a change.
            {GARAGE: complete(GARAGE, row("g1", agreement="Oui", portal_status="Accord reçu"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    first = await sync.execute()
    second = await sync.execute()

    assert first.changed == 0 and first.notifications_created == 0
    assert second.changed == 1 and second.notifications_created == 1


@pytest.mark.asyncio
async def test_downstream_appearance_adds_a_transition_event_and_never_closes_upstream_work(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[FIRST_EXPERT, FIRST_AGREED])
    factory = FakePortalReaderFactory(
        [
            {FIRST_EXPERT: complete(FIRST_EXPERT, row("c1")), FIRST_AGREED: complete(FIRST_AGREED)},
            {FIRST_EXPERT: complete(FIRST_EXPERT, row("c1")), FIRST_AGREED: complete(FIRST_AGREED, row("c1"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    await sync.execute()

    assert _event_kinds(engine, FIRST_EXPERT) == ["WORKFLOW_ITEM_TRANSITION"]
    assert _event_kinds(engine, FIRST_AGREED) == ["WORKFLOW_ITEM_NEW"]
    assert _memberships(engine, FIRST_EXPERT)["c1"][0] is True  # upstream stays active
    assert _scalar(
        engine,
        "select w.status from workflow_work w join workflow_memberships m on m.id = w.membership_id "
        "join workflows f on f.id = m.workflow_id where f.key = :k",
        k=FIRST_EXPERT,
    ) == "TO_DO"


# --- independence and failures ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_workflow_never_stops_or_invalidates_a_successful_one(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    _users(uow_factory, "alice")
    factory = FakePortalReaderFactory(
        [
            {GARAGE: complete(GARAGE, row("g1")), PHOTOS: complete(PHOTOS, row("p1"))},
            {GARAGE: failed(GARAGE, "vue introuvable"), PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
        ]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    result = await sync.execute()

    by_key = {w.workflow_key: w for w in result.workflows}
    assert by_key[GARAGE].status is PollStatus.FAILED
    assert by_key[PHOTOS].status is PollStatus.COMPLETE and by_key[PHOTOS].created == 1
    assert result.status is PollStatus.PARTIAL
    assert factory.readers[1].read_keys == [GARAGE, PHOTOS]  # the failure did not stop the loop
    # The failed workflow kept its memberships untouched; the good one was reconciled.
    assert _memberships(engine, GARAGE) == {"g1": (True, 0, 1)}
    assert _notification_kinds(engine, PHOTOS) == ["WORKFLOW_ITEM_NEW"]
    assert _scalar(engine, "select last_poll_status from workflows where key = :k", k=GARAGE) == "FAILED"
    assert _scalar(engine, "select last_poll_status from workflows where key = :k", k=PHOTOS) == "COMPLETE"
    assert _scalar(
        engine, "select workflows_failed from sync_runs order by id desc limit 1"
    ) == 1


@pytest.mark.asyncio
async def test_administrator_alerts_are_edge_triggered_and_never_touch_memberships(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE, row("g1"))}, {GARAGE: failed(GARAGE)}, {GARAGE: failed(GARAGE)}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    for _ in range(3):
        await sync.execute()

    assert _event_kinds(engine).count("WORKFLOW_POLL_FAILED") == 1  # not one per poll
    assert _memberships(engine, GARAGE) == {"g1": (True, 0, 1)}
    assert _notification_kinds(engine) == []


@pytest.mark.asyncio
async def test_auth_required_raises_one_session_alert_and_sets_the_account_state(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    factory = FakePortalReaderFactory(
        [{GARAGE: auth_required(GARAGE), PHOTOS: auth_required(PHOTOS)}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    first = await sync.execute()
    second = await sync.execute()

    assert first.status is PollStatus.AUTH_REQUIRED and second.status is PollStatus.AUTH_REQUIRED
    assert _event_kinds(engine).count("SESSION_AUTH_REQUIRED") == 1
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.AUTH_REQUIRED
    assert account.last_success_at is None


@pytest.mark.asyncio
async def test_a_reader_that_raises_is_contained_as_a_failed_workflow(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    factory = FakePortalReaderFactory(
        [{GARAGE: RuntimeError("driver crashed"), PHOTOS: complete(PHOTOS, row("p1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await sync.execute()

    by_key = {w.workflow_key: w for w in result.workflows}
    assert by_key[GARAGE].status is PollStatus.FAILED and "driver crashed" in by_key[GARAGE].error
    assert by_key[PHOTOS].status is PollStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_reconciliation_failure_rolls_back_only_that_workflow(
    uow_factory, portal_account_id, engine, clock, monkeypatch
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE, row("g1"), row("g2")), PHOTOS: complete(PHOTOS, row("p1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    original = sync._upsert_dossier
    calls = {"garage": 0}

    def flaky(uow, account_id, definition, row_, now):
        if definition.key == GARAGE:
            calls["garage"] += 1
            if calls["garage"] == 2:
                raise RuntimeError("disk full")
        return original(uow, account_id, definition, row_, now)

    monkeypatch.setattr(sync, "_upsert_dossier", flaky)

    result = await sync.execute()

    by_key = {w.workflow_key: w for w in result.workflows}
    assert by_key[GARAGE].status is PollStatus.FAILED and "disk full" in by_key[GARAGE].error
    assert by_key[PHOTOS].status is PollStatus.COMPLETE
    # Nothing of the failed workflow's transaction survived (no half-baseline).
    assert _memberships(engine, GARAGE) == {}
    assert not _baseline_done(engine, GARAGE)
    assert _memberships(engine, PHOTOS) == {"p1": (True, 0, 1)}


# --- outbox ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_are_written_to_the_outbox_in_the_same_transaction(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS)}, {PHOTOS: complete(PHOTOS, row("p1"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()
    assert _scalar(engine, "select count(*) from outbox_messages") == 0

    await sync.execute()

    assert _scalar(engine, "select count(*) from outbox_messages where topic = 'notification.created'") == 1
    assert _scalar(engine, "select count(*) from outbox_messages where topic = 'ai.job.requested'") == 0


@pytest.mark.asyncio
async def test_ai_jobs_are_queued_only_when_enabled_and_never_block_alerts(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS)}, {PHOTOS: complete(PHOTOS, row("p1"), row("p2"))}]
    )
    sync = make_sync(factory, uow_factory, clock=clock, ai_jobs_enabled=True)
    await sync.execute()

    await sync.execute()

    assert _scalar(engine, "select count(*) from outbox_messages where topic = 'ai.job.requested'") == 2
    assert _scalar(engine, "select count(*) from outbox_messages where topic = 'notification.created'") == 2


# --- detail reads ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_pages_are_read_once_per_record_per_cycle_across_workflows(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, NORMAL])
    shared = row("shared")
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE, shared, row("g2")), NORMAL: complete(NORMAL, shared)}],
        details={"shared": {"date_envoi_devis_garage": "03/02/2026 11:00"}},
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()

    assert sorted(factory.readers[0].detail_calls) == ["g2", "shared"]
    stored = _scalar(engine, "select date_envoi_devis_garage_raw from dossiers where record_id = 'shared'")
    assert stored == "03/02/2026 11:00"


@pytest.mark.asyncio
async def test_detail_reads_are_bounded_per_cycle_and_resume_on_the_next(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    rows = [row(f"g{i}") for i in range(5)]
    details = {f"g{i}": {"date_envoi_devis_garage": "01/02/2026"} for i in range(5)}
    factory = FakePortalReaderFactory([{GARAGE: complete(GARAGE, *rows)}], details=details)
    sync = make_sync(factory, uow_factory, clock=clock, max_detail_reads_per_cycle=2)

    await sync.execute()
    assert len(factory.readers[0].detail_calls) == 2
    await sync.execute()
    await sync.execute()

    total_complete = _scalar(engine, "select count(*) from dossiers where detail_complete = 1")
    assert total_complete == 5  # nothing is starved forever


@pytest.mark.asyncio
async def test_a_detail_failure_keeps_the_detection_and_is_retried(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    _users(uow_factory, "alice")
    attempts = iter([DetailReadError("page bloquée"), {"date_envoi_devis_garage": "03/02/2026"}])
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE)}, {GARAGE: complete(GARAGE, row("g1"))}],
        details={"g1": lambda: next(attempts)},
    )
    sync = make_sync(factory, uow_factory, clock=clock)
    await sync.execute()

    first = await sync.execute()

    assert first.details_failed == 1
    assert first.notifications_created == 1  # detection and alert survive the detail failure
    assert _scalar(engine, "select detail_error from dossiers") == "page bloquée"

    second = await sync.execute()  # garage refreshes every poll while the required date is missing
    assert second.details_failed == 0
    assert _scalar(engine, "select detail_complete from dossiers") == 1
    assert _scalar(engine, "select detail_error from dossiers") is None


@pytest.mark.asyncio
async def test_blank_required_quote_date_is_retried_until_populated_then_stops(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE])
    values = iter([{"date_envoi_devis_garage": ""}, {"date_envoi_devis_garage": "05/02/2026"}])
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE, row("g1"))}],
        details={"g1": lambda: next(values)},
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    for _ in range(4):
        await sync.execute()

    reads = [len(r.detail_calls) for r in factory.readers]
    assert reads == [1, 1, 0, 0]  # blank -> retried once, then populated -> never again
    assert _scalar(engine, "select date_envoi_devis_garage_raw from dossiers") == "05/02/2026"
    assert _scalar(engine, "select date_envoi_devis_garage is not null from dossiers") == 1


@pytest.mark.asyncio
async def test_non_garage_blank_required_date_is_throttled_to_its_refresh_interval(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[NORMAL])
    factory = FakePortalReaderFactory(
        [{NORMAL: complete(NORMAL, row("n1"))}], details={"n1": {"date_envoi_devis_garage": ""}}
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    clock.advance(minutes=5)
    await sync.execute()
    clock.advance(hours=2)
    await sync.execute()

    assert [len(r.detail_calls) for r in factory.readers] == [1, 0, 1]


@pytest.mark.asyncio
async def test_workflows_without_required_detail_never_open_dossier_pages(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory([{PHOTOS: complete(PHOTOS, row("p1"), row("p2"))}])
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()

    assert factory.readers[0].detail_calls == []


# --- cycle-level behaviour ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfirmed_disabled_and_uncatalogued_workflows_are_not_read(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS, NORMAL])
    set_rules(uow_factory, NORMAL, WorkflowRulesStatus.UNCONFIRMED)
    with uow_factory() as uow:
        from rma_portal.domain.models import Workflow

        uow.workflows.create(
            Workflow(
                id=None,
                portal_account_id=portal_account_id,
                key="retired_queue",
                name="Retired",
                category="x",
                route="#x/",
                view_id="view_1",
                enabled=True,
                sort_order=999,
                rules_status=WorkflowRulesStatus.CAPTURE_DERIVED,
                baseline_completed_at=None,
                last_poll_at=None,
                last_success_at=None,
                last_error=None,
            )
        )
        uow.commit()
    factory = FakePortalReaderFactory([{}])
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()

    assert factory.readers[0].read_keys == [GARAGE, PHOTOS]


@pytest.mark.asyncio
async def test_cycle_with_no_saved_session_or_no_enabled_workflow_is_a_safe_skip(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    no_session = FakePortalReaderFactory([{}], has_saved_session=False)
    result = await make_sync(no_session, uow_factory, clock=clock).execute()
    assert result.skipped and no_session.readers == []

    seed_workflows(uow_factory, only=[])
    result = await make_sync(FakePortalReaderFactory([{}]), uow_factory, clock=clock).execute()
    assert result.skipped and result.skip_reason == "no enabled workflow"
    assert _scalar(engine, "select count(*) from sync_runs") == 0


@pytest.mark.asyncio
async def test_only_one_cycle_runs_across_processes(uow_factory, portal_account_id, engine, clock):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory([{PHOTOS: complete(PHOTOS, row("p1"))}])
    lock = FakeCycleLock(acquired=False)
    sync = make_sync(factory, uow_factory, clock=clock, cycle_lock=lock)

    result = await sync.execute()

    assert result.skipped and "another" in result.skip_reason
    assert lock.attempts == 1 and factory.readers == []  # the browser was never opened
    assert _scalar(engine, "select count(*) from sync_runs") == 0

    lock.acquired = True
    assert (await sync.execute()).status is PollStatus.COMPLETE


@pytest.mark.asyncio
async def test_concurrent_executes_coalesce_onto_one_cycle(uow_factory, portal_account_id, clock):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory([{PHOTOS: complete(PHOTOS, row("p1"))}])
    sync = make_sync(factory, uow_factory, clock=clock)

    results = await asyncio.gather(sync.execute(), sync.execute(), sync.execute())

    assert len(factory.readers) == 1
    assert all(r.created == results[0].created for r in results)


@pytest.mark.asyncio
async def test_profile_lock_causes_a_safe_skip_with_no_records(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory([{PHOTOS: complete(PHOTOS, row("p1"))}], lock_held=True)
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await sync.execute()

    assert result.skipped is True
    assert _memberships(engine, PHOTOS) == {}
    assert _scalar(engine, "select count(*) from sync_runs") == 0
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.last_poll_at is None and account.last_success_at is None


@pytest.mark.asyncio
async def test_browser_launch_failure_finishes_the_cycle_as_failed_and_the_next_one_runs(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS, row("p1"))}], aenter_exception=RuntimeError("no display available")
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await sync.execute()

    assert result.status is PollStatus.FAILED and not result.skipped
    assert "RuntimeError" in result.error and "no display available" in result.error
    assert _memberships(engine, PHOTOS) == {}
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.ERROR
    assert account.last_error == result.error
    assert _scalar(engine, "select count(*) from sync_runs") == 1  # created and finished together
    assert (await sync.execute()).status is PollStatus.FAILED
    assert _scalar(engine, "select count(*) from sync_runs") == 2


@pytest.mark.asyncio
async def test_cycle_finishes_failed_when_cleanup_stalls_but_keeps_reconciled_data(
    uow_factory, portal_account_id, engine, clock, monkeypatch
):
    monkeypatch.setattr(workflow_sync_module, "_CLEANUP_TIMEOUT_SECONDS", 0.05)
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory(
        [{PHOTOS: complete(PHOTOS, row("p1"))}], aexit_gate=asyncio.Event()  # never set
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await asyncio.wait_for(sync.execute(), timeout=1.0)

    assert result.status is PollStatus.FAILED and result.created == 1
    assert sync.is_running is False
    assert _memberships(engine, PHOTOS) == {"p1": (True, 0, 1)}
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.ERROR and account.last_error


@pytest.mark.asyncio
async def test_cycle_records_its_trigger_and_parent_child_runs(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    factory = FakePortalReaderFactory([{}])
    sync = make_sync(factory, uow_factory, clock=clock)

    result = await sync.execute(SyncTrigger.MANUAL)

    assert _scalar(engine, "select trigger from sync_runs") == "MANUAL"
    assert _scalar(engine, "select count(*) from workflow_poll_runs where sync_run_id = :i", i=result.sync_run_id) == 2
    assert _scalar(engine, "select workflows_total from sync_runs") == 2


@pytest.mark.asyncio
async def test_last_success_is_only_set_by_a_fully_complete_cycle(
    uow_factory, portal_account_id, engine, clock
):
    seed_workflows(uow_factory, only=[GARAGE, PHOTOS])
    factory = FakePortalReaderFactory(
        [{GARAGE: complete(GARAGE), PHOTOS: failed(PHOTOS)}, {GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)}]
    )
    sync = make_sync(factory, uow_factory, clock=clock)

    await sync.execute()
    with uow_factory() as uow:
        assert uow.portal_accounts.get(portal_account_id).last_success_at is None
    await sync.execute()
    with uow_factory() as uow:
        assert uow.portal_accounts.get(portal_account_id).last_success_at is not None


# --- session verification ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_session_marks_ready_without_reading_any_queue(uow_factory, portal_account_id):
    seed_workflows(uow_factory, only=[PHOTOS])
    factory = FakePortalReaderFactory([{PHOTOS: complete(PHOTOS, row("p1"))}])
    sync = make_sync(factory, uow_factory)

    assert await sync.verify_session(5.0) is True

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.READY
    assert factory.readers[0].read_keys == []
    assert account.last_success_at is None  # only a full synchronization sets it


@pytest.mark.asyncio
async def test_verify_session_marks_auth_required_on_a_login_page(uow_factory, portal_account_id):
    from rma_portal.application.dto import PortalAuthRequiredError

    factory = FakePortalReaderFactory(verify_result=PortalAuthRequiredError("session expirée"))
    sync = make_sync(factory, uow_factory)

    assert await sync.verify_session(5.0) is False

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.AUTH_REQUIRED
    assert account.last_error == "session expirée"


@pytest.mark.asyncio
async def test_verify_session_times_out_with_an_actionable_error(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(verify_delay_seconds=1.0)
    sync = make_sync(factory, uow_factory)

    assert await sync.verify_session(0.05) is False

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.ERROR and account.last_error


@pytest.mark.asyncio
async def test_verify_session_is_not_kept_pending_by_a_stalled_cleanup(uow_factory, portal_account_id):
    """Startup, the auth check and cleanup are each bounded by their own wait_for."""
    factory = FakePortalReaderFactory(verify_delay_seconds=5.0, aexit_gate=asyncio.Event())
    sync = make_sync(factory, uow_factory)

    assert await asyncio.wait_for(sync.verify_session(0.02), timeout=1.0) is False

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.ERROR and account.last_error


@pytest.mark.asyncio
async def test_verify_session_never_reports_ready_when_cleanup_fails_after_a_successful_check(
    uow_factory, portal_account_id
):
    factory = FakePortalReaderFactory(aexit_gate=asyncio.Event())
    sync = make_sync(factory, uow_factory)

    assert await asyncio.wait_for(sync.verify_session(0.05), timeout=1.0) is False

    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status is SessionStatus.ERROR
    assert account.last_success_at is None


@pytest.mark.asyncio
async def test_is_running_reflects_an_in_flight_execute(uow_factory, portal_account_id):
    seed_workflows(uow_factory, only=[PHOTOS])
    sync = make_sync(FakePortalReaderFactory([{}]), uow_factory)
    assert sync.is_running is False

    task = asyncio.create_task(sync.execute())
    await asyncio.sleep(0)
    assert sync.is_running is True

    await task
    assert sync.is_running is False


@pytest.mark.asyncio
async def test_cancelling_a_solo_execute_never_leaves_an_unretrieved_future_exception(
    uow_factory, portal_account_id, caplog
):
    seed_workflows(uow_factory, only=[PHOTOS])
    sync = make_sync(FakePortalReaderFactory([{}]), uow_factory)

    task = asyncio.create_task(sync.execute())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    del task
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        gc.collect()

    assert "was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_portal_read_error_class_still_exists_for_reader_implementations():
    assert issubclass(PortalReadError, Exception)
