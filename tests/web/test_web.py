from __future__ import annotations

import time
from datetime import UTC, datetime

from starlette.testclient import TestClient

from rma_portal.application.dto import BrowserTeardownError
from rma_portal.domain.enums import PollStatus, SessionStatus
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from rma_portal.web.app import create_app
from tests.unit.infrastructure.fakes import FakeVerifySession
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


def test_same_origin_check_rejects_prefix_bypass_attempt(
    client, employee_user, uow_factory, portal_account_id
):
    """A naive Origin check (str.startswith) would wrongly accept an origin
    that merely starts with the server's own origin, e.g.
    'http://testserver.evil.example' starts with 'http://testserver'.
    """
    dossier_id = _seed_dossier(uow_factory, portal_account_id, "a")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.post(
        f"/dossiers/{dossier_id}/status",
        data={"status": "IN_PROGRESS", "expected_version": "1"},
        headers={"origin": "http://testserver.evil.example"},
    )
    assert response.status_code == 403


def test_manual_refresh_survives_a_browser_launch_failure(client, employee_user, application):
    """A Camoufox launch failure during a manual refresh must render the
    dashboard (with its ERROR banner) instead of an unhandled HTTP 500."""
    application.reader_factory._aenter_exception = RuntimeError("no display available")
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.post("/refresh", headers={"origin": "http://testserver"})

    assert response.status_code == 200
    assert "La dernière synchronisation a échoué" in response.text


def test_session_banner_shown_when_auth_required(client, employee_user, uow_factory, portal_account_id):
    with uow_factory() as uow:
        uow.portal_accounts.mark_poll_finished(
            portal_account_id,
            status=PollStatus.AUTH_REQUIRED,
            polled_at=datetime.now(UTC),
            error="reconnexion requise",
        )
        uow.commit()
        account = uow.portal_accounts.get(portal_account_id)
    assert account.session_status == SessionStatus.AUTH_REQUIRED

    login(client, "employee", EMPLOYEE_PASSWORD)
    response = client.get("/")
    assert "Votre session OmegaFlow a expiré" in response.text
    assert "Reconnecter" in response.text


def test_authenticated_user_can_start_connection_and_it_returns_immediately(
    application, employee_user, fake_open_and_wait
):
    # A background task that outlives its triggering request needs a
    # persistent event loop -- `with TestClient(app):` (a lifespan-scoped
    # BlockingPortal), not the bare `client` fixture, which tears down a
    # fresh one-shot loop after every single call.
    with TestClient(create_app(application)) as client:
        fake_open_and_wait.hold_open()
        login(client, "employee", EMPLOYEE_PASSWORD)

        response = client.post("/session/connect", headers={"origin": "http://testserver"})

        # A response came back at all (without hanging) while the fake
        # browser is still held open -- proof the endpoint did not wait
        # for it to close.
        assert response.status_code == 200
        assert "Fenêtre de connexion ouverte" in response.text
        assert "Connexion en cours" in response.text
        assert application.session_connector.is_active is True

        fake_open_and_wait.close()


def test_unauthenticated_connect_request_redirects_to_login(client):
    response = client.post(
        "/session/connect", headers={"origin": "http://testserver"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_connect_rejects_cross_origin_requests(client, employee_user):
    login(client, "employee", EMPLOYEE_PASSWORD)
    response = client.post("/session/connect", headers={"origin": "http://evil.example"})
    assert response.status_code == 403


def test_duplicate_connect_clicks_do_not_open_two_browsers(application, employee_user, fake_open_and_wait):
    with TestClient(create_app(application)) as client:
        fake_open_and_wait.hold_open()
        login(client, "employee", EMPLOYEE_PASSWORD)

        client.post("/session/connect", headers={"origin": "http://testserver"})
        client.post("/session/connect", headers={"origin": "http://testserver"})

        assert fake_open_and_wait.calls == 1
        fake_open_and_wait.close()


def test_reconnect_warning_and_button_visible_to_normal_authenticated_user(
    client, employee_user, uow_factory, portal_account_id
):
    with uow_factory() as uow:
        uow.portal_accounts.mark_poll_finished(
            portal_account_id,
            status=PollStatus.AUTH_REQUIRED,
            polled_at=datetime.now(UTC),
            error="session expirée",
        )
        uow.commit()
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.get("/")

    assert "Votre session OmegaFlow a expiré" in response.text
    assert 'hx-post="/session/connect"' in response.text


def test_application_shutdown_cancels_an_active_connection_task(
    application, employee_user, fake_open_and_wait
):
    fake_open_and_wait.hold_open()
    app = create_app(application)
    # `with TestClient(app):` runs the FastAPI lifespan (startup/shutdown),
    # unlike the `client` fixture -- needed here to exercise the shutdown
    # cleanup. The scheduler it also starts is harmless: `application`'s
    # sync_service uses FakePortalReaderFactory, never a real browser.
    with TestClient(app) as shutdown_client:
        login(shutdown_client, "employee", EMPLOYEE_PASSWORD)
        response = shutdown_client.post("/session/connect", headers={"origin": "http://testserver"})
        assert response.status_code == 200
        assert application.session_connector.is_active is True

    assert application.session_connector.is_active is False


def test_dashboard_content_disinherits_hx_select_for_session_connect_buttons(
    client, employee_user, uow_factory, portal_account_id
):
    """Regression: the periodic-refresh wrapper's hx-select="#dashboard-content"
    must not be inherited by the /session/connect buttons -- otherwise
    htmx finds no match in their session-card-only response and swaps in
    nothing, making the session card disappear after a click."""
    with uow_factory() as uow:
        uow.portal_accounts.mark_poll_finished(
            portal_account_id,
            status=PollStatus.AUTH_REQUIRED,
            polled_at=datetime.now(UTC),
            error="session expirée",
        )
        uow.commit()
    login(client, "employee", EMPLOYEE_PASSWORD)

    response = client.get("/")

    assert 'id="dashboard-content"' in response.text
    assert 'hx-disinherit="hx-select"' in response.text
    # Sanity: the disinherit sits on the same element that declares hx-select.
    content_div = response.text.split('id="dashboard-content"', 1)[1].split(">", 1)[0]
    assert 'hx-select="#dashboard-content"' in content_div
    assert 'hx-disinherit="hx-select"' in content_div


def test_dashboard_and_health_stay_responsive_while_verification_is_in_flight(
    application, employee_user, fake_open_and_wait
):
    """Regression: dashboard/login/health must remain responsive during
    browser launch, authentication verification, synchronization and
    cleanup -- proven here by holding the bounded post-login auth check
    open and confirming other routes still answer promptly."""
    verify = FakeVerifySession(result=True)
    application.session_connector = SessionConnector(
        application.settings,
        verify_session=verify,
        run_sync=application.sync_service.execute,
        mark_login_teardown_failed=application.sync_service.mark_login_teardown_failed,
        open_and_wait=fake_open_and_wait,
    )
    fake_open_and_wait.hold_open()

    with TestClient(create_app(application)) as client:
        login(client, "employee", EMPLOYEE_PASSWORD)
        response = client.post("/session/connect", headers={"origin": "http://testserver"})
        assert response.status_code == 200

        verify.hold()
        fake_open_and_wait.close()  # login window "closes" -> verification starts and hangs

        deadline = time.monotonic() + 2.0
        while application.session_connector.is_verifying is False:
            assert time.monotonic() < deadline, "verification never started"
            time.sleep(0.01)

        started = time.monotonic()
        health_response = client.get("/health")
        dashboard_response = client.get("/")
        elapsed = time.monotonic() - started

        assert health_response.status_code == 200
        assert dashboard_response.status_code == 200
        assert elapsed < 1.0  # never waited on the in-flight verification

        verify.release()


def test_login_teardown_failure_shows_error_and_restart_instruction_on_dashboard(
    application, employee_user
):
    """Regression: build_session_view must reflect a login-browser
    teardown failure immediately -- previously SessionConnector._run_login
    only marked the profile unavailable and returned, so the dashboard
    kept showing whatever READY/UNKNOWN state was persisted from before
    this connect attempt, with no sign anything had gone wrong."""

    async def open_and_wait_then_fail_teardown(settings, *, on_teardown_unconfirmed=None) -> None:
        if on_teardown_unconfirmed is not None:
            on_teardown_unconfirmed()
        raise BrowserTeardownError("teardown stalled (test)")

    application.session_connector = SessionConnector(
        application.settings,
        verify_session=application.sync_service.verify_session,
        run_sync=application.sync_service.execute,
        mark_login_teardown_failed=application.sync_service.mark_login_teardown_failed,
        open_and_wait=open_and_wait_then_fail_teardown,
    )

    with TestClient(create_app(application)) as client:
        login(client, "employee", EMPLOYEE_PASSWORD)
        response = client.post("/session/connect", headers={"origin": "http://testserver"})
        assert response.status_code == 200

        deadline = time.monotonic() + 2.0
        while application.session_connector.is_active:
            assert time.monotonic() < deadline, "login phase never finished"
            time.sleep(0.01)

        dashboard = client.get("/")

        assert "La dernière synchronisation a échoué" in dashboard.text
        assert "redémarrage du Portail RMA" in dashboard.text
        assert "Réessayer" in dashboard.text
