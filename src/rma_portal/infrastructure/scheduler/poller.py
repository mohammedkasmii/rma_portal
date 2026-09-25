"""Background 5-minute poll loop, started/stopped with the FastAPI app."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from rma_portal.application.workflow_sync import SyncWorkflows

logger = logging.getLogger(__name__)


class PollScheduler:
    def __init__(self, sync_service: SyncWorkflows, interval_seconds: int) -> None:
        self._sync_service = sync_service
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="rma-portal-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                result = await self._sync_service.execute()
                if result.skipped:
                    logger.info("poll skipped: %s", result.skip_reason)
                else:
                    logger.info(
                        "poll finished: status=%s rows=%d created=%d reactivated=%d "
                        "deactivated=%d changed=%d notified=%d details_failed=%d",
                        result.status,
                        result.rows_seen,
                        result.created,
                        result.reactivated,
                        result.deactivated,
                        result.changed,
                        result.notifications_created,
                        result.details_failed,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad poll
                logger.exception("unhandled error during scheduled poll")
            await asyncio.sleep(self._interval_seconds)
