"""Composition root: the only module that wires infrastructure into use cases.

Nothing in ``application`` or ``web`` imports SQLAlchemy or Camoufox
directly -- everything is assembled once, here, and handed down as already
constructed objects / protocol implementations.
"""

from __future__ import annotations

from dataclasses import dataclass

from rma_portal.application.accounts import AccountService
from rma_portal.application.dossier_service import DossierService
from rma_portal.application.ports import PortalReaderFactory, UnitOfWorkFactory
from rma_portal.application.sync_service import SyncAgreementQueue
from rma_portal.config import Settings, load_settings
from rma_portal.infrastructure.db.models import Base
from rma_portal.infrastructure.db.session import create_engine_for, create_session_factory
from rma_portal.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReaderFactory
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from rma_portal.infrastructure.security.password_hasher import Argon2PasswordHasher
from rma_portal.observability import configure_logging


@dataclass(slots=True)
class Application:
    settings: Settings
    uow_factory: UnitOfWorkFactory
    reader_factory: PortalReaderFactory
    sync_service: SyncAgreementQueue
    account_service: AccountService
    dossier_service: DossierService
    session_connector: SessionConnector


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or load_settings()
    settings.ensure_directories()
    settings.load_or_create_session_secret()
    configure_logging(settings)

    engine = create_engine_for(settings)
    session_factory = create_session_factory(engine)
    uow_factory = SqlAlchemyUnitOfWorkFactory(session_factory)

    reader_factory = CamoufoxPortalReaderFactory(
        profile_dir=settings.browser_profile_dir,
        lock_path=settings.browser_lock_path,
        start_route=settings.omegaflow_start_route,
        base_url=settings.omegaflow_base_url,
        procedure_value=settings.omegaflow_procedure_value,
        timezone_id=settings.portal_timezone,
        session_state_path=settings.session_state_path,
        locale=settings.portal_locale,
        headless=settings.headless_browser,
    )

    sync_service = SyncAgreementQueue(reader_factory, uow_factory)
    password_hasher = Argon2PasswordHasher()
    account_service = AccountService(uow_factory, password_hasher)
    dossier_service = DossierService(uow_factory)
    # Verifies the session immediately after the employee closes the login
    # window (bounded, no queue/enrichment read -- see
    # SyncAgreementQueue.verify_session), reusing the same
    # SyncAgreementQueue/CamoufoxPortalReader the scheduler and manual
    # refresh already use -- no second implementation of OmegaFlow
    # authentication detection. The normal synchronization then runs
    # afterward, as its own separately-tracked phase.
    session_connector = SessionConnector(
        settings,
        verify_session=sync_service.verify_session,
        run_sync=sync_service.execute,
        mark_login_teardown_failed=sync_service.mark_login_teardown_failed,
        verify_timeout_seconds=settings.session_verify_timeout_seconds,
    )

    return Application(
        settings=settings,
        uow_factory=uow_factory,
        reader_factory=reader_factory,
        sync_service=sync_service,
        account_service=account_service,
        dossier_service=dossier_service,
        session_connector=session_connector,
    )


def create_all_tables_for_tests(engine) -> None:
    """Used by tests only: production schemas come from Alembic migrations."""
    Base.metadata.create_all(engine)
