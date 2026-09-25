from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from rma_portal.application.accounts import AccountService
from rma_portal.application.ai import AdvisorService
from rma_portal.application.dossier_service import DossierService
from rma_portal.application.outbox import OutboxProcessor
from rma_portal.application.work_service import WorkService
from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import Role
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from rma_portal.infrastructure.portal.workflow_catalog import default_catalog
from rma_portal.infrastructure.security.password_hasher import Argon2PasswordHasher
from rma_portal.web.app import create_app
from tests.support import make_sync, seed_workflows
from tests.unit.application.fakes import FakePortalReaderFactory
from tests.unit.infrastructure.fakes import FakeOpenAndWait

ADMIN_PASSWORD = "CorrectHorse1"
EMPLOYEE_PASSWORD = "AnotherHorse2"


@pytest.fixture
def fake_open_and_wait() -> FakeOpenAndWait:
    return FakeOpenAndWait()


@pytest.fixture
def application(settings, uow_factory, portal_account_id, fake_open_and_wait) -> Application:
    seed_workflows(uow_factory, only=["agreement_garage"])
    reader_factory = FakePortalReaderFactory([])
    hasher = Argon2PasswordHasher()
    account_service = AccountService(uow_factory, hasher)
    dossier_service = DossierService(uow_factory)
    sync_service = make_sync(reader_factory, uow_factory)
    session_connector = SessionConnector(
        settings,
        verify_session=sync_service.verify_session,
        run_sync=sync_service.execute,
        mark_login_teardown_failed=sync_service.mark_login_teardown_failed,
        open_and_wait=fake_open_and_wait,
    )
    settings.session_secret = "test-secret-not-for-production"
    work_service = WorkService(
        uow_factory, default_catalog(), base_url="https://omegaflow.example", connect_url="https://vnc.example/"
    )
    return Application(
        settings=settings,
        uow_factory=uow_factory,
        reader_factory=reader_factory,
        sync_service=sync_service,
        catalog_sync=WorkflowCatalogSync(uow_factory, default_catalog()),
        work_service=work_service,
        outbox_processor=OutboxProcessor(uow_factory, {}),
        outbox_handlers={},
        advisor_service=AdvisorService(uow_factory, work_service, None),
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
