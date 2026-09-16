from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from rma_portal.application.dossier_service import DossierService
from rma_portal.application.dto import (
    DetailReadError,
    DossierDetails,
    PortalAuthRequiredError,
    PortalPartialReadError,
    PortalReadError,
    QueueRow,
    QueueSnapshot,
)
from rma_portal.application.sync_service import SyncAgreementQueue
from rma_portal.domain.enums import PollStatus, WorkStatus
from rma_portal.domain.models import DossierDates, WorkStatusConflict
from tests.unit.application.fakes import FakePortalReaderFactory


def _row(record_id: str, **overrides) -> QueueRow:
    base = {
        "record_id": record_id,
        "dossier_number": f"DOS-{record_id}",
        "insured_name": "Client Synthétique",
        "procedure": "Garage agréé",
        "registration": "0000-A-00",
        "garage": "Garage Synthétique",
        "estimate_amount_raw": "1 000,00 MAD",
        "portal_status": "En instance",
        "city": "Casablanca",
        "observation_count": "0",
        "agreement_login": "login-test",
        "details_href": f"#dossiers-en-instance-accord/view-dossier-details/{record_id}",
    }
    base.update(overrides)
    return QueueRow(**base)


def _create_user(uow_factory, username: str):
    from rma_portal.application.accounts import AccountService

    class _StubHasher:
        def hash(self, password: str) -> str:
            return f"hashed:{password}"

        def verify(self, password_hash: str, password: str) -> bool:
            return password_hash == f"hashed:{password}"

    service = AccountService(uow_factory, _StubHasher())
    return service.create_user(
        username=username, display_name=username, password="password123", role=__import__(
            "rma_portal.domain.enums", fromlist=["Role"]
        ).Role.EMPLOYEE
    )


@pytest.mark.asyncio
async def test_first_complete_poll_creates_no_notifications(uow_factory, portal_account_id):
    user = _create_user(uow_factory, "alice")
    factory = FakePortalReaderFactory(polls=[QueueSnapshot(rows=(_row("a"), _row("b")), pages_seen=1)])
    sync = SyncAgreementQueue(factory, uow_factory)

    result = await sync.execute()

    assert result.status == PollStatus.COMPLETE
    assert result.created == 2
    assert result.notifications_created == 0
    with uow_factory() as uow:
        assert uow.notifications.unread_count_for_user(user.id) == 0


@pytest.mark.asyncio
async def test_unseen_id_after_baseline_notifies_every_existing_user(uow_factory, portal_account_id):
    alice = _create_user(uow_factory, "alice")
    bob = _create_user(uow_factory, "bob")
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(_row("a"),), pages_seen=1),
            QueueSnapshot(rows=(_row("a"), _row("new")), pages_seen=1),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()  # baseline
    result = await sync.execute()

    assert result.created == 1
    assert result.notifications_created == 1
    with uow_factory() as uow:
        assert uow.notifications.unread_count_for_user(alice.id) == 1
        assert uow.notifications.unread_count_for_user(bob.id) == 1


@pytest.mark.asyncio
async def test_employee_acknowledges_while_other_remains_unread(uow_factory, portal_account_id):
    alice = _create_user(uow_factory, "alice")
    bob = _create_user(uow_factory, "bob")
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(), pages_seen=1),
            QueueSnapshot(rows=(_row("new"),), pages_seen=1),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()
    await sync.execute()

    with uow_factory() as uow:
        dossier = uow.dossiers.get_by_record_id(portal_account_id, "new")
    dossier_service = DossierService(uow_factory)
    dossier_service.acknowledge(dossier.id, alice.id)

    with uow_factory() as uow:
        assert uow.notifications.is_unread_for_user(dossier.id, alice.id) is False
        assert uow.notifications.is_unread_for_user(dossier.id, bob.id) is True


@pytest.mark.asyncio
async def test_new_user_inherits_no_historical_unread_notifications(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(), pages_seen=1),
            QueueSnapshot(rows=(_row("new"),), pages_seen=1),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()
    await sync.execute()

    late_user = _create_user(uow_factory, "carla")
    with uow_factory() as uow:
        assert uow.notifications.unread_count_for_user(late_user.id) == 0


def test_shared_work_status_and_notes(uow_factory, portal_account_id):
    alice = _create_user(uow_factory, "alice")
    bob = _create_user(uow_factory, "bob")
    with uow_factory() as uow:
        dossier = uow.dossiers.create_from_row(portal_account_id, _row("a"), datetime.now(UTC))
        uow.commit()
        dossier_id = dossier.id

    service = DossierService(uow_factory)
    service.update_work_status(
        dossier_id=dossier_id, status=WorkStatus.IN_PROGRESS, expected_version=1, user_id=alice.id
    )
    service.add_note(dossier_id=dossier_id, author_id=bob.id, body="Note partagée de test")

    dossier, work, notes = service.get_dossier(dossier_id)
    assert work.status == WorkStatus.IN_PROGRESS
    assert work.updated_by == alice.id
    assert len(notes) == 1
    assert notes[0].author_id == bob.id


def test_optimistic_work_status_version_conflict(uow_factory, portal_account_id):
    alice = _create_user(uow_factory, "alice")
    with uow_factory() as uow:
        dossier = uow.dossiers.create_from_row(portal_account_id, _row("a"), datetime.now(UTC))
        uow.commit()
        dossier_id = dossier.id

    service = DossierService(uow_factory)
    service.update_work_status(
        dossier_id=dossier_id, status=WorkStatus.IN_PROGRESS, expected_version=1, user_id=alice.id
    )
    with pytest.raises(WorkStatusConflict):
        service.update_work_status(
            dossier_id=dossier_id, status=WorkStatus.DONE, expected_version=1, user_id=alice.id
        )


@pytest.mark.asyncio
async def test_detail_failure_preserves_detection_and_retries(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[QueueSnapshot(rows=(_row("a"),), pages_seen=1), QueueSnapshot(rows=(_row("a"),), pages_seen=1)],
        details_by_id={"a": DetailReadError("boom")},
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    result = await sync.execute()
    assert result.details_failed == 1
    with uow_factory() as uow:
        dossier = uow.dossiers.get_by_record_id(portal_account_id, "a")
    assert dossier is not None  # detection survives despite detail failure
    assert dossier.detail_complete is False

    # Second poll retries the failed detail, this time succeeding.
    factory._details_by_id["a"] = DossierDetails(dates=DossierDates(), detail_complete=True)
    result2 = await sync.execute()
    assert result2.details_failed == 0
    with uow_factory() as uow:
        dossier = uow.dossiers.get_by_record_id(portal_account_id, "a")
    assert dossier.detail_complete is True


@pytest.mark.asyncio
async def test_partial_poll_preserves_previous_data(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(_row("a"), _row("b")), pages_seen=1),
            PortalPartialReadError("network blip", partial=QueueSnapshot(rows=(), pages_seen=0)),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()
    result = await sync.execute()

    assert result.status == PollStatus.PARTIAL
    with uow_factory() as uow:
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert state["a"].active is True
    assert state["b"].active is True
    assert state["a"].missing_complete_polls == 0


@pytest.mark.asyncio
async def test_auth_required_preserves_previous_data(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(_row("a"),), pages_seen=1),
            PortalAuthRequiredError("reconnect"),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()
    result = await sync.execute()

    assert result.status == PollStatus.AUTH_REQUIRED
    with uow_factory() as uow:
        state = uow.dossiers.existing_state_by_account(portal_account_id)
        account = uow.portal_accounts.get(portal_account_id)
    assert state["a"].active is True
    assert account.session_status.value == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_one_missing_complete_poll_keeps_dossier_active(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(_row("a"),), pages_seen=1),
            QueueSnapshot(rows=(), pages_seen=1),
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    await sync.execute()
    await sync.execute()

    with uow_factory() as uow:
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert state["a"].active is True
    assert state["a"].missing_complete_polls == 1


@pytest.mark.asyncio
async def test_two_missing_complete_polls_deactivate_and_reappearance_creates_new_occurrence(
    uow_factory, portal_account_id
):
    user = _create_user(uow_factory, "alice")
    factory = FakePortalReaderFactory(
        polls=[
            QueueSnapshot(rows=(_row("a"),), pages_seen=1),  # baseline
            QueueSnapshot(rows=(), pages_seen=1),  # miss 1
            QueueSnapshot(rows=(), pages_seen=1),  # miss 2 -> deactivate
            QueueSnapshot(rows=(_row("a"),), pages_seen=1),  # reappears -> new occurrence
        ]
    )
    sync = SyncAgreementQueue(factory, uow_factory)
    for _ in range(3):
        await sync.execute()

    with uow_factory() as uow:
        state = uow.dossiers.existing_state_by_account(portal_account_id)
    assert state["a"].active is False

    result = await sync.execute()
    assert result.reactivated == 1
    assert result.notifications_created == 1
    with uow_factory() as uow:
        state = uow.dossiers.existing_state_by_account(portal_account_id)
        assert uow.notifications.unread_count_for_user(user.id) == 1
    assert state["a"].active is True


@pytest.mark.asyncio
async def test_concurrent_refreshes_execute_only_once(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(polls=[QueueSnapshot(rows=(_row("a"),), pages_seen=1)])
    sync = SyncAgreementQueue(factory, uow_factory)

    results = await asyncio.gather(sync.execute(), sync.execute(), sync.execute())

    assert len(factory.readers) == 1
    assert all(r.created == results[0].created for r in results)


@pytest.mark.asyncio
async def test_profile_lock_causes_a_safe_skip(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(
        polls=[QueueSnapshot(rows=(_row("a"),), pages_seen=1)], lock_held=True
    )
    sync = SyncAgreementQueue(factory, uow_factory)

    result = await sync.execute()

    assert result.skipped is True
    with uow_factory() as uow:
        assert uow.dossiers.existing_state_by_account(portal_account_id) == {}
        account = uow.portal_accounts.get(portal_account_id)
    assert account.last_success_at is None


@pytest.mark.asyncio
async def test_portal_read_error_marks_poll_failed(uow_factory, portal_account_id):
    factory = FakePortalReaderFactory(polls=[PortalReadError("cannot reach portal")])
    sync = SyncAgreementQueue(factory, uow_factory)

    result = await sync.execute()

    assert result.status == PollStatus.FAILED
    with uow_factory() as uow:
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status.value == "ERROR"
    assert account.last_error == "cannot reach portal"
