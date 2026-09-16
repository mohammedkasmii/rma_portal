from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from rma_portal.application.accounts import AccountService
from rma_portal.application.dossier_service import DossierService
from rma_portal.application.sync_service import SyncAgreementQueue
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import Role
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from rma_portal.infrastructure.security.password_hasher import Argon2PasswordHasher
from rma_portal.web.app import create_app
from tests.unit.application.fakes import FakePortalReaderFactory
from tests.unit.infrastructure.fakes import FakeOpenAndWait

ADMIN_PASSWORD = "CorrectHorse1"
EMPLOYEE_PASSWORD = "AnotherHorse2"


@pytest.fixture
def fake_open_and_wait() -> FakeOpenAndWait:
    return FakeOpenAndWait()


@pytest.fixture
def application(settings, uow_factory, portal_account_id, fake_open_and_wait) -> Application:
    reader_factory = FakePortalReaderFactory(polls=[])
    hasher = Argon2PasswordHasher()
    account_service = AccountService(uow_factory, hasher)
    dossier_service = DossierService(uow_factory)
    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    session_connector = SessionConnector(
        settings,
        verify_session=sync_service.verify_session,
        run_sync=sync_service.execute,
        open_and_wait=fake_open_and_wait,
    )
    settings.session_secret = "test-secret-not-for-production"
    return Application(
        settings=settings,
        uow_factory=uow_factory,
        reader_factory=reader_factory,
        sync_service=sync_service,
        account_service=account_service,
        dossier_service=dossier_service,
        session_connector=session_connector,
    )


@pytest.fixture
def admin_user(application):
    return application.account_service.create_user(
        username="admin", display_name="Admin Test", password=ADMIN_PASSWORD, role=Role.ADMIN
    )


@pytest.fixture
def employee_user(application):
    return application.account_service.create_user(
        username="employee", display_name="Employee Test", password=EMPLOYEE_PASSWORD, role=Role.EMPLOYEE
    )


@pytest.fixture
def client(application) -> TestClient:
    # Deliberately not used as `with TestClient(app):` -- that would run the
    # FastAPI lifespan and start the real poller. Routes work fine without it.
    app = create_app(application)
    return TestClient(app)


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    cookie = response.cookies.get("rma_portal_session")
    assert cookie
    client.cookies.set("rma_portal_session", cookie)
    return cookie
