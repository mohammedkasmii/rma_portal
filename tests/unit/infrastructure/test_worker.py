"""The production worker loop: schedule, on-demand cycles, outbox, heartbeat, resilience."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pytest

from rma_portal.application.dto import SyncResult
from rma_portal.domain.enums import OutboxTopic, PollStatus, SyncTrigger
from rma_portal.infrastructure.db.advisory_lock import NullCycleLock, build_cycle_lock
from rma_portal.infrastructure.scheduler.worker import WorkerLoop, heartbeat_age_seconds


class _Ticker:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _loop(uow_factory, triggers: list, *, ticker=None, handlers=None, heartbeat=None, interval=300.0, error=None):
    ticker = ticker or _Ticker()

    async def sync(trigger: SyncTrigger) -> SyncResult:
        triggers.append(trigger)
        if error is not None:
            raise error
        return SyncResult(status=PollStatus.COMPLETE)

    loop = WorkerLoop(
        sync=sync,
        uow_factory=uow_factory,
        outbox_handlers=handlers or {},
        interval_seconds=interval,
        heartbeat_path=heartbeat,
        clock=ticker,
    )
    return loop, ticker


@pytest.mark.asyncio
async def test_a_cycle_runs_immediately_then_only_when_the_interval_elapsed(uow_factory):
    triggers: list[SyncTrigger] = []
    loop, clock = _loop(uow_factory, triggers)

    await loop.run_once()  # due at start
    clock.now += 100
    await loop.run_once()  # not due yet
    clock.now += 250
    await loop.run_once()  # 350 s since the last cycle

    assert triggers == [SyncTrigger.SCHEDULED, SyncTrigger.SCHEDULED]


@pytest.mark.asyncio
async def test_an_employee_request_from_the_api_triggers_an_immediate_manual_cycle(uow_factory):
    triggers: list[SyncTrigger] = []
    loop, clock = _loop(uow_factory, triggers)
    await loop.run_once()
    with uow_factory() as uow:
        uow.outbox.add(OutboxTopic.SYNC_REQUESTED, {}, datetime.now(UTC))
        uow.commit()
    clock.now += 5  # far from the next scheduled cycle

    await loop.run_once()

    assert triggers == [SyncTrigger.SCHEDULED, SyncTrigger.MANUAL]
    with uow_factory() as uow:
        assert uow.outbox.pending_count(OutboxTopic.SYNC_REQUESTED) == 0
    # The request also pushes the next scheduled cycle a full interval away.
    clock.now += 100
    await loop.run_once()
    assert len(triggers) == 2


@pytest.mark.asyncio
async def test_several_queued_requests_collapse_into_one_cycle(uow_factory):
    triggers: list[SyncTrigger] = []
    loop, clock = _loop(uow_factory, triggers)
    await loop.run_once()
    with uow_factory() as uow:
        for _ in range(3):
            uow.outbox.add(OutboxTopic.SYNC_REQUESTED, {}, datetime.now(UTC))
        uow.commit()
    clock.now += 1

    await loop.run_once()

    assert triggers.count(SyncTrigger.MANUAL) == 1


@pytest.mark.asyncio
async def test_a_failing_cycle_never_stops_the_worker_and_is_retried_on_schedule(uow_factory):
    triggers: list[SyncTrigger] = []
    loop, clock = _loop(uow_factory, triggers, error=RuntimeError("database restarted"))

    await loop.run_once()
    clock.now += 10
    await loop.run_once()  # not due: the failed cycle still consumed its slot
    clock.now += 400
    await loop.run_once()

    assert triggers == [SyncTrigger.SCHEDULED, SyncTrigger.SCHEDULED]


@pytest.mark.asyncio
async def test_outbox_messages_are_delivered_by_the_worker_even_between_cycles(uow_factory):
    seen: list[int] = []

    async def deliver(message) -> None:
        seen.append(message.payload["notification_id"])

    loop, clock = _loop(uow_factory, [], handlers={"notification.created": deliver})
    await loop.run_once()
    with uow_factory() as uow:
        uow.outbox.add(OutboxTopic.NOTIFICATION_CREATED, {"notification_id": 7}, datetime.now(UTC))
        uow.commit()
    clock.now += 1

    await loop.run_once()

    assert seen == [7]


@pytest.mark.asyncio
async def test_a_broken_outbox_handler_does_not_stop_cycles(uow_factory):
    async def explode(message) -> None:
        raise RuntimeError("ollama down")

    triggers: list[SyncTrigger] = []
    loop, clock = _loop(uow_factory, triggers, handlers={"ai.job.requested": explode})
    with uow_factory() as uow:
        uow.outbox.add(OutboxTopic.AI_JOB_REQUESTED, {"occurrence_id": 1}, datetime.now(UTC))
        uow.commit()

    await loop.run_once()

    assert triggers == [SyncTrigger.SCHEDULED]


@pytest.mark.asyncio
async def test_every_tick_touches_the_heartbeat_the_health_check_reads(uow_factory, tmp_path):
    beat = tmp_path / "state" / "worker.heartbeat"
    loop, _ = _loop(uow_factory, [], heartbeat=beat)

    assert heartbeat_age_seconds(beat) is None
    await loop.run_once()

    assert beat.exists() and heartbeat_age_seconds(beat) < 5
    os.utime(beat, (time.time() - 500, time.time() - 500))
    assert heartbeat_age_seconds(beat) > 400


@pytest.mark.asyncio
async def test_stop_request_ends_run_forever_promptly(uow_factory):
    import asyncio

    triggers: list[SyncTrigger] = []
    loop, _ = _loop(uow_factory, triggers)
    loop._tick = 0.01

    task = asyncio.create_task(loop.run_forever())
    await asyncio.sleep(0.05)
    loop.request_stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert triggers  # it ran at least the first cycle before stopping


def test_worker_health_command_reflects_the_heartbeat(tmp_path, monkeypatch, capsys):
    from rma_portal.cli import main

    monkeypatch.setenv("RMA_PORTAL_DATA_DIR", str(tmp_path))

    assert main(["worker-health"]) == 1  # never beat -> unhealthy
    (tmp_path / "worker.heartbeat").touch()
    assert main(["worker-health"]) == 0
    old = time.time() - 3600
    os.utime(tmp_path / "worker.heartbeat", (old, old))
    assert main(["worker-health", "--max-age", "120"]) == 1
    assert "stale" in capsys.readouterr().err


def test_sqlite_deployments_use_the_null_cycle_lock(engine):
    lock = build_cycle_lock(engine)

    assert isinstance(lock, NullCycleLock)
    with lock.try_acquire() as first, lock.try_acquire() as second:
        assert first is True and second is True  # single process: the asyncio lock is the guard
