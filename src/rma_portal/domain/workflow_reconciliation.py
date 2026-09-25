"""Pure notification and change policy for one workflow.

No I/O. ``application.workflow_sync`` reads the portal and the repositories,
then asks this module what each observation *means*:

============================  ==========================  ==============================
observation                   ACTION workflow             INFORMATIONAL workflow
============================  ==========================  ==============================
first appearance, baseline    silent occurrence           silent occurrence
first appearance, after       ``NEW`` + unread alert      ``COMPLETED`` activity
reappearance                  ``RETURNED`` + unread       ``RETURNED`` activity
material change               ``CHANGED``; unread only    ``CHANGED`` activity
                              when a status/date/action
                              field changed
absent on two complete polls  ``LEFT`` activity           ``LEFT`` activity
============================  ==========================  ==============================

``SILENT`` workflows are synchronized for search and detail context only: they
produce occurrences and memberships but no events and no alerts. Nothing here
infers an overdue or SLA alert from a captured date -- no SLA has been confirmed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from rma_portal.domain.enums import NotificationClass, NotificationKind, WorkflowEventKind
from rma_portal.domain.workflow_definition import WorkflowDefinition

_FINGERPRINT_LENGTH = 12


def normalize_value(value: str) -> str:
    return " ".join(value.split())


def fingerprint_value(key: str, value: str) -> str:
    """A short fingerprint of one field value: enough to show that it changed and to
    correlate identical values, without duplicating the customer payload."""
    digest = hashlib.sha256(f"{key}\x1f{normalize_value(value)}".encode()).hexdigest()
    return digest[:_FINGERPRINT_LENGTH]


def fingerprint_fields(fields: Mapping[str, str]) -> str:
    """A stable 64-character fingerprint of every captured field of a membership."""
    payload = json.dumps(
        {key: normalize_value(value) for key, value in sorted(fields.items())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MaterialChange:
    changed_fields: tuple[str, ...]
    before: Mapping[str, str]
    """Field key -> fingerprint of the previous value."""

    after: Mapping[str, str]
    alerting: bool
    """True when a key status, key date or action-availability field changed."""


def detect_material_change(
    before: Mapping[str, str],
    after: Mapping[str, str],
    *,
    material_fields: frozenset[str],
    alert_fields: frozenset[str],
) -> MaterialChange | None:
    """Compare the stored captured fields with the freshly read ones.

    Only fields that were captured before are compared: a field the stored
    membership has never seen (for example one added by a newer catalog version)
    establishes its own baseline instead of being reported as a change.
    """
    changed = tuple(
        sorted(
            key
            for key in material_fields
            if key in before and normalize_value(before[key]) != normalize_value(after.get(key, ""))
        )
    )
    if not changed:
        return None
    return MaterialChange(
        changed_fields=changed,
        before={key: fingerprint_value(key, before[key]) for key in changed},
        after={key: fingerprint_value(key, after.get(key, "")) for key in changed},
        alerting=any(key in alert_fields for key in changed),
    )


@dataclass(frozen=True, slots=True)
class Decision:
    """What to record for one observation. ``event`` None means: record nothing."""

    event: WorkflowEventKind | None = None
    notification: NotificationKind | None = None


_NOTHING = Decision()


def decide_first_appearance(notification_class: NotificationClass, *, alerts_enabled: bool) -> Decision:
    """A dossier that has no membership in this workflow yet."""
    if notification_class is NotificationClass.SILENT or not alerts_enabled:
        return _NOTHING
    if notification_class is NotificationClass.ACTION:
        return Decision(WorkflowEventKind.WORKFLOW_ITEM_NEW, NotificationKind.WORKFLOW_ITEM_NEW)
    return Decision(WorkflowEventKind.WORKFLOW_ITEM_COMPLETED)


def decide_return(notification_class: NotificationClass, *, alerts_enabled: bool) -> Decision:
    """An inactive membership (two complete omissions) that reappears."""
    if notification_class is NotificationClass.SILENT or not alerts_enabled:
        return _NOTHING
    if notification_class is NotificationClass.ACTION:
        return Decision(
            WorkflowEventKind.WORKFLOW_ITEM_RETURNED, NotificationKind.WORKFLOW_ITEM_RETURNED
        )
    return Decision(WorkflowEventKind.WORKFLOW_ITEM_RETURNED)


def decide_change(
    notification_class: NotificationClass, change: MaterialChange | None, *, alerts_enabled: bool
) -> Decision:
    if change is None or notification_class is NotificationClass.SILENT or not alerts_enabled:
        return _NOTHING
    if notification_class is NotificationClass.ACTION and change.alerting:
        return Decision(
            WorkflowEventKind.WORKFLOW_ITEM_CHANGED, NotificationKind.WORKFLOW_ITEM_CHANGED
        )
    return Decision(WorkflowEventKind.WORKFLOW_ITEM_CHANGED)


def decide_departure(notification_class: NotificationClass, *, alerts_enabled: bool) -> Decision:
    """A membership deactivated after two consecutive complete omissions."""
    if notification_class is NotificationClass.SILENT or not alerts_enabled:
        return _NOTHING
    return Decision(WorkflowEventKind.WORKFLOW_ITEM_LEFT)


def counts_as_actionable_new(kind: NotificationKind) -> bool:
    """The dashboard's "actionable new" counter: arrivals, never changes."""
    return kind in (
        NotificationKind.WORKFLOW_ITEM_NEW,
        NotificationKind.WORKFLOW_ITEM_RETURNED,
        NotificationKind.NEW_AGREEMENT_DOSSIER,
    )


def needs_detail_read(
    definition: WorkflowDefinition,
    detail_fields: Mapping[str, str],
    last_attempt: datetime | None,
    *,
    failed: bool,
    forced: bool,
    now: datetime,
) -> bool:
    """Whether a dossier's shared detail page should be read for this workflow.

    Only workflows that declare ``detail_required`` fields ever cause a read. A
    new, returned or status-changed dossier (``forced``) and one never attempted
    are read at once. Otherwise the page is re-read only while a required value is
    still blank (or the previous attempt failed) and at most once per the
    workflow's refresh interval -- Garage agréé keeps its historical "re-read while
    the quote date is blank" behaviour with an interval of zero.
    """
    if not definition.detail_required:
        return False
    if forced or last_attempt is None:
        return True
    if (now - last_attempt).total_seconds() < definition.detail_refresh_seconds:
        return False
    if failed:
        return True
    return any(not detail_fields.get(key, "").strip() for key in definition.detail_required)
