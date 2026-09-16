from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rma_portal.config import Settings
from rma_portal.domain.enums import SessionStatus
from rma_portal.infrastructure.db.models import Base, PortalAccountRow
from rma_portal.infrastructure.db.session import create_engine_for, create_session_factory
from rma_portal.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "rma-portal-data")


@pytest.fixture
def engine(settings: Settings):
    engine = create_engine_for(settings)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def uow_factory(engine):
    session_factory = create_session_factory(engine)
    return SqlAlchemyUnitOfWorkFactory(session_factory)


@pytest.fixture
def portal_account_id(engine) -> int:
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        row = PortalAccountRow(
            name="OmegaFlow",
            base_url="https://omegaflow.example",
            enabled=True,
            baseline_completed_at=None,
            session_status=SessionStatus.UNKNOWN,
            last_poll_at=None,
            last_success_at=None,
            last_error=None,
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def utcnow() -> datetime:
    return datetime.now(UTC)
