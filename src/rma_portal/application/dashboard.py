"""Pure dashboard search/filter/sort rules.

Kept out of the SQL layer on purpose: the agency's dossier volume is small
(a LAN tool for one team), so fetching every active dossier once and
filtering/sorting in Python is simpler to get right and to test than
reproducing the same rules in SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from rma_portal.application.dto import DashboardRow
from rma_portal.domain.enums import WorkStatus


@dataclass(frozen=True, slots=True)
class DashboardCounts:
    new_for_me: int
    to_do: int
    in_progress: int
    waiting: int
    done: int


def _matches_search(row: DashboardRow, needle: str) -> bool:
    haystack = " ".join(
        (row.dossier_number, row.insured_name, row.registration, row.garage)
    ).casefold()
    return needle.casefold() in haystack


def filter_and_sort(
    rows: Iterable[DashboardRow],
    *,
    search: str | None = None,
    unread_only: bool = False,
    work_status: WorkStatus | None = None,
    portal_status: str | None = None,
) -> list[DashboardRow]:
    result = list(rows)
    if search:
        result = [row for row in result if _matches_search(row, search)]
    if unread_only:
        result = [row for row in result if row.unread]
    if work_status is not None:
        result = [row for row in result if row.work_status == work_status]
    if portal_status:
        result = [row for row in result if row.portal_status == portal_status]

    def sort_key(row: DashboardRow) -> tuple:
        return (
            0 if row.unread else 1,
            -_epoch(row.date_envoi_devis_garage),
            -_epoch(row.detection_time),
        )

    return sorted(result, key=sort_key)


def _epoch(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    return value.timestamp()


def compute_counts(rows: Iterable[DashboardRow]) -> DashboardCounts:
    rows = list(rows)
    return DashboardCounts(
        new_for_me=sum(1 for r in rows if r.unread),
        to_do=sum(1 for r in rows if r.work_status == WorkStatus.TO_DO),
        in_progress=sum(1 for r in rows if r.work_status == WorkStatus.IN_PROGRESS),
        waiting=sum(1 for r in rows if r.work_status == WorkStatus.WAITING),
        done=sum(1 for r in rows if r.work_status == WorkStatus.DONE),
    )
