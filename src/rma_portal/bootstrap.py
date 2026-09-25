"""Composition root: the only module that wires infrastructure into use cases.

Nothing in ``application`` or ``web`` imports SQLAlchemy or Camoufox
directly -- everything is assembled once, here, and handed down as already
constructed objects / protocol implementations.
"""

from __future__ import annotations

from dataclasses import dataclass

from rma_portal.application.accounts import AccountService
from rma_portal.application.ai import AdvisorService, AiJobHandler
from rma_portal.application.dossier_service import DossierService
from rma_portal.application.outbox import OutboxHandler, OutboxProcessor, log_delivery
from rma_portal.application.ports import PortalReaderFactory, UnitOfWorkFactory
from rma_portal.application.work_service import WorkService
from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
from rma_portal.application.workflow_sync import SyncWorkflows
from rma_portal.config import Settings, load_settings
from rma_portal.domain.enums import OutboxTopic, SyncTrigger
from rma_portal.infrastructure.ai.ollama import build_advisor
from rma_portal.infrastructure.db.advisory_lock import build_cycle_lock
from rma_portal.infrastructure.db.models import Base
from rma_portal.infrastructure.db.session import create_engine_for, create_session_factory
from rma_portal.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from rma_portal.infrastructure.portal.camoufox_reader import CamoufoxPortalReaderFactory
from rma_portal.infrastructure.portal.session_connector import SessionConnector
from rma_portal.infrastructure.portal.workflow_catalog import default_catalog
from rma_portal.infrastructure.security.password_hasher import Argon2PasswordHasher
from rma_portal.observability import configure_logging


@dataclass(slots=True)
class Application:
    settings: Settings
    uow_factory: UnitOfWorkFactory
    reader_factory: PortalReaderFactory
    sync_service: SyncWorkflows
    catalog_sync: WorkflowCatalogSync
    work_service: WorkService
    outbox_processor: OutboxProcessor
    outbox_handlers: dict[str, OutboxHandler]
    advisor_service: AdvisorService
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
        timezone_id=settings.portal_timezone,
        session_state_path=settings.session_state_path,
        locale=settings.portal_locale,
        headless=settings.headless_browser,
    )

    catalog = default_catalog()
    catalog_sync = WorkflowCatalogSync(uow_factory, catalog)
    advisor = build_advisor(
        enabled=settings.ollama_enabled,
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    sync_service = SyncWorkflows(
        reader_factory,
        uow_factory,
        catalog,
        timezone_id=settings.portal_timezone,
        max_detail_reads_per_cycle=settings.max_detail_reads_per_cycle,
        ai_jobs_enabled=advisor is not None,
        cycle_lock=build_cycle_lock(engine),
    )
    password_hasher = Argon2PasswordHasher()
    account_service = AccountService(uow_factory, password_hasher)
    work_service = WorkService(
        uow_factory,
        catalog,
        base_url=settings.omegaflow_base_url,
        timezone_id=settings.portal_timezone,
        connect_url=settings.novnc_url or None,
    )
    advisor_service = AdvisorService(uow_factory, work_service, advisor)
    outbox_handlers: dict[str, OutboxHandler] = {
        OutboxTopic.NOTIFICATION_CREATED.value: log_delivery,
        OutboxTopic.WORKFLOW_EVENT_RECORDED.value: log_delivery,
        OutboxTopic.AI_JOB_REQUESTED.value: AiJobHandler(advisor_service),
    }
    outbox_processor = OutboxProcessor(uow_factory, outbox_handlers)
    dossier_service = DossierService(uow_factory)
    # Verifies the session immediately after the employee closes the login
    # window (bounded, no queue/enrichment read -- see
    # SyncWorkflows.verify_session), reusing the same
    # SyncWorkflows/CamoufoxPortalReader the scheduler and manual
    # refresh already use -- no second implementation of OmegaFlow
    # authentication detection. The normal synchronization then runs
    # afterward, as its own separately-tracked phase.
    session_connector = SessionConnector(
        settings,
        verify_session=sync_service.verify_session,
        run_sync=lambda: sync_service.execute(SyncTrigger.CONNECT),
        mark_login_teardown_failed=sync_service.mark_login_teardown_failed,
        is_sync_running=lambda: sync_service.is_running,
        verify_timeout_seconds=settings.session_verify_timeout_seconds,
    )

    return Application(
        settings=settings,
        uow_factory=uow_factory,
        reader_factory=reader_factory,
        sync_service=sync_service,
        catalog_sync=catalog_sync,
        work_service=work_service,
        outbox_processor=outbox_processor,
        outbox_handlers=outbox_handlers,
        advisor_service=advisor_service,
        account_service=account_service,
        dossier_service=dossier_service,
        session_connector=session_connector,
    )


def create_all_tables_for_tests(engine) -> None:
    """Used by tests only: production schemas come from Alembic migrations."""
    Base.metadata.create_all(engine)
