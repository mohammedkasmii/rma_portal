from __future__ import annotations

from datetime import UTC, datetime

from rma_portal.domain.enums import SessionStatus
from tests.web.conftest import ADMIN_PASSWORD, EMPLOYEE_PASSWORD, login


def test_health_endpoint_requires_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_success(client, admin_user):
    response = client.post(
        "/login", data={"username": "admin", "password": ADMIN_PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "rma_portal_session" in response.cookies


def test_login_failure_wrong_password(client, admin_user):
    response = client.post("/login", data={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401
    assert "Identifiants invalides" in response.text


def test_inactive_user_is_rejected(client, employee_user, application):
    application.account_service.set_active(
        user_id=employee_user.id, active=False, acting_admin_id=999
    )
    response = client.post(
        "/login", data={"username": "employee", "password": EMPLOYEE_PASSWORD}
    )
    assert response.status_code == 401


def test_employee_cannot_access_admin_pages(client, employee_user):
    login(client, "employee", EMPLOYEE_PASSWORD)
    response = client.get("/admin/users")
    assert response.status_code == 403


def test_admin_can_access_admin_pages(client, admin_user):
    login(client, "admin", ADMIN_PASSWORD)
    response = client.get("/admin/users")
    assert response.status_code == 200


def test_admin_cannot_disable_own_active_account(client, admin_user):
    login(client, "admin", ADMIN_PASSWORD)
    response = client.post(
        f"/admin/users/{admin_user.id}/toggle-active",
        data={"active": "0"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert "désactiver votre propre compte" in response.text


def _seed_dossier(uow_factory, portal_account_id, record_id: str, **overrides):
    from tests.unit.application.test_sync_service import _row

    with uow_factory() as uow:
        dossier = uow.dossiers.create_from_row(
            portal_account_id, _row(record_id, **overrides), datetime.now(UTC)
        )
        uow.commit()
        return dossier.id


def test_dashboard_requires_login_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303


def test_dashboard_search_and_filters(client, employee_user, uow_factory, portal_account_id):
    _seed_dossier(uow_factory, portal_account_id, "a", dossier_number="DOS-AAA", insured_name="Karim")
    _seed_dossier(uow_factory, portal_account_id, "b", dossier_number="DOS-BBB", insured_name="Sara")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.get("/", params={"search": "Karim"})
    assert response.status_code == 200
    assert "DOS-AAA" in response.text
    assert "DOS-BBB" not in response.text


def test_acknowledgement_is_per_employee(client, uow_factory, portal_account_id, application):
    from rma_portal.domain.enums import Role

    alice = application.account_service.create_user(
        username="alice", display_name="Alice", password="AlicePassword1", role=Role.EMPLOYEE
    )
    bob = application.account_service.create_user(
        username="bob", display_name="Bob", password="BobPassword1", role=Role.EMPLOYEE
    )
    dossier_id = _seed_dossier(uow_factory, portal_account_id, "a")
    with uow_factory() as uow:
        from rma_portal.domain.enums import NotificationKind

        uow.notifications.create(dossier_id, NotificationKind.NEW_AGREEMENT_DOSSIER, datetime.now(UTC))
        uow.commit()

    login(client, "alice", "AlicePassword1")
    response = client.get(f"/dossiers/{dossier_id}")
    assert response.status_code == 200

    with uow_factory() as uow:
        assert uow.notifications.is_unread_for_user(dossier_id, alice.id) is False
        assert uow.notifications.is_unread_for_user(dossier_id, bob.id) is True


def test_update_work_status_and_add_note(client, employee_user, uow_factory, portal_account_id):
    dossier_id = _seed_dossier(uow_factory, portal_account_id, "a")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.post(
        f"/dossiers/{dossier_id}/status",
        data={"status": "IN_PROGRESS", "expected_version": "1"},
        follow_redirects=False,
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 303

    response = client.post(
        f"/dossiers/{dossier_id}/notes",
        data={"body": "Ceci est une note de test."},
        follow_redirects=False,
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 303

    page = client.get(f"/dossiers/{dossier_id}")
    assert "Ceci est une note de test." in page.text
    assert 'selected' in page.text


def test_same_origin_check_rejects_missing_origin(client, employee_user, uow_factory, portal_account_id):
    dossier_id = _seed_dossier(uow_factory, portal_account_id, "a")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.post(
        f"/dossiers/{dossier_id}/status",
        data={"status": "IN_PROGRESS", "expected_version": "1"},
    )
    assert response.status_code == 403


def test_same_origin_check_rejects_foreign_origin(client, employee_user, uow_factory, portal_account_id):
    dossier_id = _seed_dossier(uow_factory, portal_account_id, "a")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.post(
        f"/dossiers/{dossier_id}/status",
        data={"status": "IN_PROGRESS", "expected_version": "1"},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_session_banner_shown_when_auth_required(client, employee_user, uow_factory, portal_account_id):
    with uow_factory() as uow:
        uow.portal_accounts.mark_poll_finished(
            portal_account_id,
            status=__import__("rma_portal.domain.enums", fromlist=["PollStatus"]).PollStatus.AUTH_REQUIRED,
            polled_at=datetime.now(UTC),
            error="reconnexion requise",
        )
        uow.commit()
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status == SessionStatus.AUTH_REQUIRED

    login(client, "employee", EMPLOYEE_PASSWORD)
    response = client.get("/")
    assert "session OmegaFlow doit être reconnectée" in response.text
