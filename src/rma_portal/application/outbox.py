"""Reliable processing of the transactional outbox.

Reconciliation writes an outbox row in the *same* transaction as the alert it
announces, so a crash can never leave an alert without its follow-up message
or a message without its alert. This processor delivers pending messages
afterwards, at-least-once: a handler that raises is retried with exponential
backoff (see ``OutboxRepository.mark_failed``) and never blocks the messages
behind it or the synchronization that produced them.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from rma_portal.application.ports import UnitOfWorkFactory
from rma_portal.domain.models import OutboxMessage

logger = logging.getLogger(__name__)

OutboxHandler = Callable[[OutboxMessage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OutboxResult:
    processed: int = 0
    failed: int = 0
    unhandled: int = 0


async def log_delivery(message: OutboxMessage) -> None:
    """Default handler: the portal has no push channel yet, so delivery means "recorded".

    Alerts are read from the database by the API; the message proves the alert
    was committed and gives future channels (web push, e-mail) a hook."""
    logger.info("outbox topic=%s id=%s delivered", message.topic, message.id)


class OutboxProcessor:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        handlers: Mapping[str, OutboxHandler],
        *,
        batch_size: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._handlers = dict(handlers)
        self._batch_size = batch_size
        self._clock = clock

    async def process_pending(self) -> OutboxResult:
        with self._uow_factory() as uow:
            pending = uow.outbox.claim_pending(self._clock(), self._batch_size)

        processed = failed = unhandled = 0
        for message in pending:
            assert message.id is not None
            handler = self._handlers.get(message.topic)
            if handler is None:
                # Not this process's topic (for example sync.requested belongs to the
                # scheduler): leave it pending for its owner.
                unhandled += 1
                continue
            try:
                await handler(message)
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop the rest
                logger.warning("outbox message %s (%s) failed", message.id, message.topic, exc_info=True)
                with self._uow_factory() as uow:
                    uow.outbox.mark_failed(message.id, f"{type(exc).__name__}: {exc}", self._clock())
                    uow.commit()
                failed += 1
                continue
            with self._uow_factory() as uow:
                uow.outbox.mark_processed(message.id, self._clock())
                uow.commit()
            processed += 1
        return OutboxResult(processed=processed, failed=failed, unhandled=unhandled)
