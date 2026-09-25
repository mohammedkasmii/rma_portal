"""In-memory PortalReader/PortalReaderFactory test doubles for ``SyncWorkflows``.

They implement the exact protocols in ``application.ports`` so a synchronization
cycle can be exercised against a real (SQLite) database without Camoufox or a
network call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from rma_portal.application.dto import (
    BrowserProfileLockedError,
    DossierDetailValues,
    PortalDossierRef,
    WorkflowQueueRow,
    WorkflowReadOutcome,
    WorkflowSnapshot,
)
from rma_portal.domain.enums import PollStatus
from rma_portal.domain.workflow_definition import FieldSpec, WorkflowDefinition

ReadScript = WorkflowReadOutcome | Exception
DetailScript = Mapping[str, str] | Exception | Callable[[], Mapping[str, str] | Exception]


def row(record_id: str, **fields: str) -> WorkflowQueueRow:
    return WorkflowQueueRow(
        record_id=record_id,
        details_href=f"#queue/view-dossier-details/{record_id}/",
        fields={"dossier_number": f"D-{record_id}", "portal_status": "En cours", **fields},
    )


def complete(key: str, *rows: WorkflowQueueRow, pages: int = 1) -> WorkflowReadOutcome:
    return WorkflowReadOutcome(
        key, PollStatus.COMPLETE, WorkflowSnapshot(key, tuple(rows), pages_seen=pages), None
    )


def partial(key: str, *rows: WorkflowQueueRow, error: str = "page 3 obsolète") -> WorkflowReadOutcome:
    return WorkflowReadOutcome(
        key, PollStatus.PARTIAL, WorkflowSnapshot(key, tuple(rows), pages_seen=2), error
    )


def failed(key: str, error: str = "vue introuvable") -> WorkflowReadOutcome:
    return WorkflowReadOutcome(key, PollStatus.FAILED, WorkflowSnapshot(key), error)


def auth_required(key: str, error: str = "session expirée") -> WorkflowReadOutcome:
    return WorkflowReadOutcome(key, PollStatus.AUTH_REQUIRED, WorkflowSnapshot(key), error)


@dataclass
class FakePortalReader:
    """One authenticated session: ``script`` maps a workflow key to its scripted read."""

    script: dict[str, ReadScript] = field(default_factory=dict)
    details: dict[str, DetailScript] = field(default_factory=dict)
    lock_held: bool = False
    aenter_exception: Exception | None = None
    verify_result: Exception | None = None
    verify_delay_seconds: float = 0.0
    aexit_gate: asyncio.Event | None = None
    read_keys: list[str] = field(default_factory=list)
    detail_calls: list[str] = field(default_factory=list)

    async def __aenter__(self) -> FakePortalReader:
        # A real Camoufox launch always yields control at least once; this keeps
        # concurrency tests meaningful (two execute() calls really overlap).
        await asyncio.sleep(0)
        if self.lock_held:
            raise BrowserProfileLockedError("profile locked (test)")
        if self.aenter_exception is not None:
            raise self.aenter_exception
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # ``aexit_gate`` simulates a stalled browser teardown.
        if self.aexit_gate is not None:
            await self.aexit_gate.wait()

    async def read_workflow(self, definition: WorkflowDefinition) -> WorkflowReadOutcome:
        await asyncio.sleep(0)
        self.read_keys.append(definition.key)
        scripted = self.script.get(definition.key)
        if scripted is None:
            return complete(definition.key)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    async def read_dossier_detail_fields(
        self, dossier: PortalDossierRef, fields: Sequence[FieldSpec]
    ) -> DossierDetailValues:
        self.detail_calls.append(dossier.record_id)
        outcome = self.details.get(dossier.record_id)
        if outcome is None:
            return DossierDetailValues({})
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return DossierDetailValues(dict(outcome))

    async def verify_authenticated(self) -> None:
        if self.verify_delay_seconds:
            await asyncio.sleep(self.verify_delay_seconds)
        if self.verify_result is not None:
            raise self.verify_result


class FakePortalReaderFactory:
    """Hands out one :class:`FakePortalReader` per ``open()`` (one per cycle).

    ``cycles`` is consumed in order; the last entry repeats once exhausted so a test
    that only cares about the first cycles does not have to script the rest.
    """

    def __init__(
        self,
        cycles: list[dict[str, ReadScript]] | None = None,
        *,
        details: dict[str, DetailScript] | None = None,
        lock_held: Callable[[], bool] | bool = False,
        aenter_exception: Exception | None = None,
        verify_result: Exception | None = None,
        verify_delay_seconds: float = 0.0,
        aexit_gate: asyncio.Event | None = None,
        has_saved_session: bool = True,
    ) -> None:
        self._cycles = list(cycles or [])
        self._details = details if details is not None else {}
        self._lock_held = lock_held
        self._aenter_exception = aenter_exception
        self._verify_result = verify_result
        self._verify_delay_seconds = verify_delay_seconds
        self._aexit_gate = aexit_gate
        self._has_saved_session = has_saved_session
        self.readers: list[FakePortalReader] = []

    def has_saved_session(self) -> bool:
        return self._has_saved_session

    def open(self) -> FakePortalReader:
        held = self._lock_held() if callable(self._lock_held) else self._lock_held
        if len(self._cycles) > 1:
            script = self._cycles.pop(0)
        else:
            script = self._cycles[0] if self._cycles else {}
        reader = FakePortalReader(
            script=script,
            details=self._details,
            lock_held=held,
            aenter_exception=self._aenter_exception,
            verify_result=self._verify_result,
            verify_delay_seconds=self._verify_delay_seconds,
            aexit_gate=self._aexit_gate,
        )
        self.readers.append(reader)
        return reader

