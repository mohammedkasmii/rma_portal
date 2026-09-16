from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from rma_portal.config import Settings


def _enable_wal(dbapi_connection: sqlite3.Connection, connection_record: object) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_engine_for(settings: Settings) -> Engine:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, future=True)
    event.listen(engine, "connect", _enable_wal)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
