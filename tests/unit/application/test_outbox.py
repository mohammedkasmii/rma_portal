"""Outbox processing: at-least-once delivery that never blocks or loses messages."""

from __future__ import annotations

from datetime import timedelta

import pytest

from rma_portal.application.outbox import OutboxProcessor
from tests.support import StepClock


def _enqueue(uow_factory, topic: str, clock, **payload) -> int:
    with uow_factory() as uow:
        message = uow.outbox.add(topic, payload, clock())
        uow.commit()
        return message.id


@pytest.mark.asyncio
async def test_delivers_pending_messages_once_and_in_order(uow_factory):
    clock = StepClock()
    first = _enqueue(uow_factory, "notification.created", clock, notification_id=1)
    second = _enqueue(uow_factory, "notification.created", clock, notification_id=2)
    seen: list[int] = []

    async def handler(message):
        seen.append(message.id)

    processor = OutboxProcessor(uow_factory, {"notification.created": handler}, clock=clock)

    first_run = await processor.process_pending()
    second_run = await processor.process_pending()

    assert seen == [first, second]
    assert (first_run.processed, second_run.processed) == (2, 0)


@pytest.mark.asyncio
async def test_a_failing_handler_backs_off_and_does_not_block_the_others(uow_factory):
    clock = StepClock()
    bad = _enqueue(uow_factory, "ai.job.requested", clock, occurrence_id=1)
    good = _enqueue(uow_factory, "notification.created", clock, notification_id=1)
    delivered: list[int] = []

    async def explode(message):
        raise RuntimeError("ollama is down")

    async def deliver(message):
        delivered.append(message.id)

    processor = OutboxProcessor(
        uow_factory, {"ai.job.requested": explode, "notification.created": deliver}, clock=clock
    )

    result = await processor.process_pending()

    assert (result.processed, result.failed) == (1, 1)
    assert delivered == [good]
    with uow_factory() as uow:
        assert uow.outbox.claim_pending(clock()) == []  # backing off, not dropped
        retry = uow.outbox.claim_pending(clock() + timedelta(hours=2))
        assert [m.id for m in retry] == [bad]
        assert "ollama is down" in retry[0].last_error


@pytest.mark.asyncio
async def test_topics_without_a_handler_stay_pending_for_their_owner(uow_factory):
    clock = StepClock()
    _enqueue(uow_factory, "sync.requested", clock)
    processor = OutboxProcessor(uow_factory, {}, clock=clock)

    result = await processor.process_pending()

    assert (result.processed, result.unhandled) == (0, 1)
    with uow_factory() as uow:
        assert uow.outbox.pending_count("sync.requested") == 1
