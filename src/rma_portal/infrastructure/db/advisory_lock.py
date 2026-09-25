"""Cross-process "only one synchronization cycle" locks.

PostgreSQL uses a *session-level advisory lock* on a dedicated connection: it is
released the moment the holder finishes, and -- crucially -- also when the holder
crashes or its connection dies, so a dead worker can never wedge the queue.
SQLite deployments (the Windows installation) run a single process and use a
no-op lock.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

SYNC_CYCLE_LOCK_KEY = 0x524D410001  # "RMA" + 1: unique to this application's poll cycle


class NullCycleLock:
    """Single-process deployments: the in-process asyncio lock is the only guard needed."""

    @contextmanager
    def try_acquire(self) -> Generator[bool]:
        yield True


class PostgresAdvisoryCycleLock:
    def __init__(self, engine: Engine, key: int = SYNC_CYCLE_LOCK_KEY) -> None:
        self._engine = engine
        self._key = key

    @contextmanager
    def try_acquire(self) -> Generator[bool]:
        connection = self._engine.connect()
        try:
            acquired = bool(
                connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}).scalar()
            )
            # Autobegin opened a transaction; the session-level lock outlives it, but keep the
            # connection out of "idle in transaction" while the (long) cycle runs.
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})
                        connection.commit()
                    except Exception:  # noqa: BLE001 - discard the session so the lock cannot leak
                        logger.warning("advisory unlock failed; discarding the connection to release it")
                        connection.invalidate()
        finally:
            connection.close()


def build_cycle_lock(engine: Engine):
    """The right lock for the configured database."""
    if engine.dialect.name == "postgresql":
        return PostgresAdvisoryCycleLock(engine)
    return NullCycleLock()
