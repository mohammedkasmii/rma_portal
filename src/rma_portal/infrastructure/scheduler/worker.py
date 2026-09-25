"""The production worker process: scheduled cycles, on-demand cycles and the outbox.

One instance runs next to the API (never inside it). Every ``tick_seconds`` it

* processes pending outbox messages (alerts, optional AI jobs);
* starts a synchronization cycle when the interval elapsed or an employee asked for
  one (an ``sync.requested`` outbox message written by the API);
* touches a heartbeat file the container health check reads.

Only one cycle can run across all processes: ``SyncWorkflows`` takes a PostgreSQL
advisory lock, so a second worker (or a restart overlap) simply skips its turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from rma_portal.application.dto import SyncResult
from rma_portal.application.outbox import OutboxHandler, OutboxProcessor
from rma_portal.application.ports import UnitOfWorkFactory
from rma_portal.domain.enums import OutboxTopic, SyncTrigger

logger = logging.getLogger(__name__)


class WorkerLoop:
    def __init__(
        self,
        *,
        sync: Callable[[SyncTrigger], Awaitable[SyncResult]],
        uow_factory: UnitOfWorkFactory,
        outbox_handlers: dict[str, OutboxHandler],
        interval_seconds: float,
        heartbeat_path: Path | None = None,
        tick_seconds: float = 2.0,
        catalog_sync: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sync = sync
        self._uow_factory = uow_factory
        self._interval = interval_seconds
        self._heartbeat_path = heartbeat_path
        self._tick = tick_seconds
        self._catalog_sync = catalog_sync
        self._clock = clock
        self._requested = False
        handlers = dict(outbox_handlers)
        handlers[OutboxTopic.SYNC_REQUESTED.value] = self._on_sync_requested
        self._outbox = OutboxProcessor(uow_factory, handlers)
        self._next_due = clock()
        self._stop = asyncio.Event()

    async def _on_sync_requested(self, message) -> None:
        self._requested = True

    def request_stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> SyncResult | None:
        """One tick: drain the outbox, then run a cycle if one is due. Never raises."""
        cycle: SyncResult | None = None
        try:
            await self._outbox.process_pending()
        except Exception:  # noqa: BLE001 - the loop must survive a database hiccup
            logger.exception("outbox processing failed")
        due = self._clock() >= self._next_due
        if self._requested or due:
            trigger = SyncTrigger.MANUAL if self._requested else SyncTrigger.SCHEDULED
            self._requested = False
            try:
                cycle = await self._sync(trigger)
                self._log(cycle)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle must not stop the worker
                logger.exception("unhandled error during synchronization cycle")
            self._next_due = self._clock() + self._interval
        self._beat()
        return cycle

    async def run_forever(self) -> None:
        if self._catalog_sync is not None:
            with contextlib.suppress(Exception):
                self._catalog_sync()
        logger.info("worker started interval_s=%s", self._interval)
        while not self._stop.is_set():
            await self.run_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
        logger.info("worker stopped")

    def _beat(self) -> None:
        if self._heartbeat_path is None:
            return
        with contextlib.suppress(OSError):
            self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self._heartbeat_path.touch()

    @staticmethod
    def _log(result: SyncResult) -> None:
        if result.skipped:
            logger.info("cycle skipped: %s", result.skip_reason)
        else:
            logger.info(
                "cycle finished: status=%s rows=%d created=%d reactivated=%d deactivated=%d "
                "changed=%d notified=%d details_failed=%d workflows=%d",
                result.status,
                result.rows_seen,
                result.created,
                result.reactivated,
                result.deactivated,
                result.changed,
                result.notifications_created,
                result.details_failed,
                len(result.workflows),
            )


def heartbeat_age_seconds(path: Path) -> float | None:
    """Seconds since the worker last beat, or ``None`` when it never did."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None
