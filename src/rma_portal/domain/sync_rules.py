"""Pure OmegaFlow reconciliation policy.

This module contains no I/O. ``application.sync_service.SyncAgreementQueue``
reads the portal and the repositories, then calls :func:`reconcile` to decide
what must change, and finally persists the result. Keeping the policy pure
makes every rule in the specification independently testable without a
database or a browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rma_portal.domain.enums import MAX_MISSING_COMPLETE_POLLS, PollStatus


@dataclass(frozen=True, slots=True)
class ExistingDossierState:
    """The minimal state of a previously known dossier needed to reconcile."""

    record_id: str
    active: bool
    missing_complete_polls: int


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    poll_status: PollStatus
    baseline_already_completed: bool
    seen_record_ids: frozenset[str]
    existing: Mapping[str, ExistingDossierState]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Actions the caller must apply. All sets are stable-sorted for tests."""

    to_create: frozenset[str]
    """Record IDs never seen before: create a dossier row."""

    to_reactivate: frozenset[str]
    """Record IDs that were inactive and reappeared: reactivate + notify."""

    to_touch: frozenset[str]
    """Record IDs already active and seen again: refresh fields only."""

    absence_increments: Mapping[str, int]
    """Record ID -> new missing_complete_polls value (COMPLETE polls only)."""

    to_deactivate: frozenset[str]
    """Record IDs crossing the absence threshold this poll."""

    create_notifications: bool
    """False before/at baseline: no NEW_AGREEMENT_DOSSIER events are raised."""

    is_baseline_poll: bool
    """True when this poll is the first COMPLETE poll (sets baseline_completed_at)."""

    reconciled: bool
    """False for AUTH_REQUIRED/FAILED polls: nothing above is meaningful."""


_EMPTY = frozenset[str]()


def reconcile(data: ReconciliationInput) -> ReconciliationResult:
    """Decide dossier lifecycle transitions for one poll.

    Rules (see docs/architecture.md for the narrative version):

    * AUTH_REQUIRED / FAILED: no queue data was usable. Nothing changes.
    * The first COMPLETE poll (baseline not yet completed) stores every seen
      dossier and raises zero notifications.
    * After baseline, a record ID never seen before is created and notified.
    * A known *active* record seen again is only refreshed.
    * A known *inactive* record seen again is reactivated and re-notified
      (a new "occurrence").
    * Only a COMPLETE poll may increment or reset absence counters or
      deactivate a dossier; PARTIAL polls leave absence bookkeeping alone.
    * A dossier crosses to inactive after
      :data:`~rma_portal.domain.enums.MAX_MISSING_COMPLETE_POLLS` consecutive
      COMPLETE polls without being seen.
    """

    if data.poll_status in (PollStatus.AUTH_REQUIRED, PollStatus.FAILED):
        return ReconciliationResult(
            to_create=_EMPTY,
            to_reactivate=_EMPTY,
            to_touch=_EMPTY,
            absence_increments={},
            to_deactivate=_EMPTY,
            create_notifications=False,
            is_baseline_poll=False,
            reconciled=False,
        )

    is_baseline_poll = (
        not data.baseline_already_completed and data.poll_status is PollStatus.COMPLETE
    )
    create_notifications = data.baseline_already_completed

    to_create: set[str] = set()
    to_reactivate: set[str] = set()
    to_touch: set[str] = set()

    for record_id in data.seen_record_ids:
        existing = data.existing.get(record_id)
        if existing is None:
            to_create.add(record_id)
        elif existing.active:
            to_touch.add(record_id)
        else:
            to_reactivate.add(record_id)

    absence_increments: dict[str, int] = {}
    to_deactivate: set[str] = set()
    if data.poll_status is PollStatus.COMPLETE:
        for record_id, existing in data.existing.items():
            if not existing.active or record_id in data.seen_record_ids:
                continue
            new_count = existing.missing_complete_polls + 1
            absence_increments[record_id] = new_count
            if new_count >= MAX_MISSING_COMPLETE_POLLS:
                to_deactivate.add(record_id)

    return ReconciliationResult(
        to_create=frozenset(to_create),
        to_reactivate=frozenset(to_reactivate),
        to_touch=frozenset(to_touch),
        absence_increments=absence_increments,
        to_deactivate=frozenset(to_deactivate),
        create_notifications=create_notifications,
        is_baseline_poll=is_baseline_poll,
        reconciled=True,
    )
