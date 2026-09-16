"""In-memory PortalReader/PortalReaderFactory test doubles.

These implement the exact protocols in ``application.ports`` so
``SyncAgreementQueue`` can be exercised without Camoufox or a network call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    DossierDetails,
    PortalDossierRef,
    QueueSnapshot,
)


@dataclass
class ScriptedPoll:
    """One entry consumed per call to ``read_agreement_queue``."""

    result: QueueSnapshot | Exception


@dataclass
class FakePortalReader:
    poll: ScriptedPoll
    details_by_id: dict[str, DossierDetails | Exception | Callable[[], DossierDetails | Exception]] = field(
        default_factory=dict
    )
    lock_held: bool = False
    aenter_exception: Exception | None = None
    verify_delay_seconds: float = 0.0
    read_calls: list[str] = field(default_factory=list)

    async def __aenter__(self) -> FakePortalReader:
        # A real Camoufox launch always yields control at least once; this
        # keeps concurrency tests meaningful (two `execute()` calls actually
        # overlap instead of running fully sequentially without ever
        # suspending).
        await asyncio.sleep(0)
        if self.lock_held:
            raise BrowserProfileLockedError("profile locked (test)")
        if self.aenter_exception is not None:
            raise self.aenter_exception
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def read_agreement_queue(self) -> QueueSnapshot:
        if isinstance(self.poll.result, Exception):
            raise self.poll.result
        return self.poll.result

    async def verify_authenticated(self) -> None:
        # Same scripted outcome as read_agreement_queue: an Exception (e.g.
        # PortalAuthRequiredError) means "not authenticated"; anything else
        # means the saved session passes the check. `verify_delay_seconds`
        # lets a test simulate a slow/hanging check to exercise
        # SyncAgreementQueue.verify_session's bounded timeout.
        if self.verify_delay_seconds:
            await asyncio.sleep(self.verify_delay_seconds)
        if isinstance(self.poll.result, Exception):
            raise self.poll.result

    async def read_dossier_details(self, dossier: PortalDossierRef) -> DossierDetails:
        self.read_calls.append(dossier.record_id)
        outcome = self.details_by_id.get(dossier.record_id)
        if outcome is None:
            return DossierDetails(dates=_empty_dates(), detail_complete=True, detail_error=None)
        if callable(outcome):
            # Lets a test return a different DossierDetails on each call,
            # e.g. a closure over an iterator, to simulate a value that only
            # appears on a later poll (see test_missing_required_quote_date_*).
            outcome = outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _empty_dates():
    from rma_portal.domain.models import DossierDates

    return DossierDates()


class FakePortalReaderFactory:
    """Hands out one :class:`FakePortalReader` per ``open()`` call.

    ``polls`` is consumed in order (one per sync). ``details_by_id`` and
    ``lock_held`` apply to every reader produced.
    """

    def __init__(
        self,
        polls: list[QueueSnapshot | Exception],
        details_by_id: dict[str, DossierDetails | Exception | Callable[[], DossierDetails | Exception]]
        | None = None,
        lock_held: Callable[[], bool] | bool = False,
        aenter_exception: Exception | None = None,
        verify_delay_seconds: float = 0.0,
    ) -> None:
        self._polls = list(polls)
        self._details_by_id = details_by_id or {}
        self._lock_held = lock_held
        self._aenter_exception = aenter_exception
        self._verify_delay_seconds = verify_delay_seconds
        self.readers: list[FakePortalReader] = []

    def open(self) -> FakePortalReader:
        held = self._lock_held() if callable(self._lock_held) else self._lock_held
        result = self._polls.pop(0) if self._polls else QueueSnapshot()
        reader = FakePortalReader(
            poll=ScriptedPoll(result),
            details_by_id=self._details_by_id,
            lock_held=held,
            aenter_exception=self._aenter_exception,
            verify_delay_seconds=self._verify_delay_seconds,
        )
        self.readers.append(reader)
        return reader
