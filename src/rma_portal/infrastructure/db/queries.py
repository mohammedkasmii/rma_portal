"""SQLAlchemy read-side queries backing the employee JSON API.

One transaction shares these with the repositories (``uow.queries``). Volumes are
small (one agency, a few thousand memberships), so each method issues a handful of
set-based queries and assembles the read models in Python rather than encoding
presentation rules in SQL.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from rma_portal.application.read_models import (
    DossierHit,
    EventView,
    InboxRecord,
    WorkflowStat,
)
from rma_portal.domain.enums import (
    OPERATIONAL_EVENT_KINDS,
    NotificationClass,
    NotificationKind,
    WorkStatus,
)
from rma_portal.infrastructure.db.models import (
    DossierRow,
    NotificationReadRow,
    NotificationRow,
    WorkflowEventRow,
    WorkflowMembershipRow,
    WorkflowOccurrenceRow,
    WorkflowPollRunRow,
    WorkflowRow,
    WorkflowWorkRow,
)
from rma_portal.infrastructure.db.repositories import _detail_fields


class SqlAlchemyWorkQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- inbox ---------------------------------------------------------------------------------

    def _unread_by_membership(
        self, user_id: int, membership_ids: Iterable[int] | None = None
    ) -> dict[int, dict[NotificationKind, int]]:
        statement = (
            select(WorkflowOccurrenceRow.membership_id, NotificationRow.kind, func.count())
            .select_from(NotificationRow)
            .join(
                WorkflowOccurrenceRow,
                WorkflowOccurrenceRow.id == NotificationRow.workflow_occurrence_id,
            )
            .outerjoin(
                NotificationReadRow,
                (NotificationReadRow.notification_id == NotificationRow.id)
                & (NotificationReadRow.user_id == user_id),
            )
            .where(NotificationReadRow.notification_id.is_(None))
            .group_by(WorkflowOccurrenceRow.membership_id, NotificationRow.kind)
        )
        if membership_ids is not None:
            statement = statement.where(
                WorkflowOccurrenceRow.membership_id.in_(list(membership_ids))
            )
        result: dict[int, dict[NotificationKind, int]] = defaultdict(dict)
        for membership_id, kind, count in self._session.execute(statement):
            result[membership_id][kind] = count
        return result

    def inbox_records(
        self,
        user_id: int,
        *,
        workflow_ids: list[int] | None = None,
        dossier_id: int | None = None,
        active_only: bool = True,
    ) -> list[InboxRecord]:
        statement = (
            select(
                WorkflowMembershipRow,
                WorkflowRow,
                DossierRow,
                WorkflowWorkRow,
                WorkflowOccurrenceRow,
            )
            .join(WorkflowRow, WorkflowRow.id == WorkflowMembershipRow.workflow_id)
            .join(DossierRow, DossierRow.id == WorkflowMembershipRow.dossier_id)
            .outerjoin(WorkflowWorkRow, WorkflowWorkRow.membership_id == WorkflowMembershipRow.id)
            .join(
                WorkflowOccurrenceRow,
                (WorkflowOccurrenceRow.membership_id == WorkflowMembershipRow.id)
                & (WorkflowOccurrenceRow.occurrence_number == WorkflowMembershipRow.occurrence_number),
            )
        )
        if workflow_ids is not None:
            statement = statement.where(WorkflowMembershipRow.workflow_id.in_(workflow_ids))
        if dossier_id is not None:
            statement = statement.where(WorkflowMembershipRow.dossier_id == dossier_id)
        if active_only:
            statement = statement.where(WorkflowMembershipRow.active.is_(True))
        rows = self._session.execute(statement).all()
        unread = self._unread_by_membership(user_id, [r[0].id for r in rows] if dossier_id else None)

        records = []
        for membership, workflow, dossier, work, occurrence in rows:
            records.append(
                InboxRecord(
                    workflow_id=workflow.id,
                    workflow_key=workflow.key,
                    workflow_name=workflow.name,
                    workflow_category=workflow.category,
                    workflow_route=workflow.route,
                    notification_class=workflow.notification_class,
                    rules_status=workflow.rules_status,
                    membership_id=membership.id,
                    dossier_id=dossier.id,
                    record_id=dossier.record_id,
                    active=membership.active,
                    first_seen_at=membership.first_seen_at,
                    last_seen_at=membership.last_seen_at,
                    last_changed_at=membership.last_changed_at,
                    captured_fields=json.loads(membership.captured_fields_json or "{}"),
                    occurrence_id=occurrence.id,
                    occurrence_number=occurrence.occurrence_number,
                    occurrence_origin=occurrence.origin,
                    occurrence_detected_at=occurrence.detected_at,
                    dossier_number=dossier.dossier_number,
                    insured_name=dossier.insured_name,
                    procedure=dossier.procedure,
                    registration=dossier.registration,
                    garage=dossier.garage,
                    portal_status=dossier.portal_status,
                    city=dossier.city,
                    details_href=dossier.details_href,
                    detail_fields=_detail_fields(dossier),
                    work_status=work.status if work else WorkStatus.TO_DO,
                    work_version=work.version if work else 1,
                    work_updated_at=work.updated_at if work else None,
                    work_updated_by=work.updated_by if work else None,
                    unread_counts=unread.get(membership.id, {}),
                )
            )
        return records

    def unread_occurrence_ids(self, user_id: int, dossier_id: int) -> set[int]:
        statement = (
            select(NotificationRow.workflow_occurrence_id)
            .outerjoin(
                NotificationReadRow,
                (NotificationReadRow.notification_id == NotificationRow.id)
                & (NotificationReadRow.user_id == user_id),
            )
            .where(
                NotificationRow.dossier_id == dossier_id,
                NotificationReadRow.notification_id.is_(None),
                NotificationRow.workflow_occurrence_id.is_not(None),
            )
            .distinct()
        )
        return {row for row in self._session.execute(statement).scalars() if row is not None}

    def occurrence_owner(self, occurrence_id: int) -> tuple[int, int] | None:
        """``(workflow_id, dossier_id)`` of an occurrence, or ``None`` when unknown."""
        row = self._session.execute(
            select(WorkflowOccurrenceRow.workflow_id, WorkflowOccurrenceRow.dossier_id).where(
                WorkflowOccurrenceRow.id == occurrence_id
            )
        ).first()
        return (row[0], row[1]) if row else None

    def membership_owner(self, membership_id: int) -> tuple[int, int] | None:
        row = self._session.execute(
            select(WorkflowMembershipRow.workflow_id, WorkflowMembershipRow.dossier_id).where(
                WorkflowMembershipRow.id == membership_id
            )
        ).first()
        return (row[0], row[1]) if row else None

    # --- workflows -----------------------------------------------------------------------------

    def workflow_stats(self, user_id: int, account_id: int) -> list[WorkflowStat]:
        workflows = self._session.execute(
            select(WorkflowRow)
            .where(WorkflowRow.portal_account_id == account_id)
            .order_by(WorkflowRow.sort_order, WorkflowRow.id)
        ).scalars().all()
        ids = [w.id for w in workflows]
        if not ids:
            return []

        active_counts = dict(
            self._session.execute(
                select(WorkflowMembershipRow.workflow_id, func.count())
                .where(WorkflowMembershipRow.workflow_id.in_(ids), WorkflowMembershipRow.active.is_(True))
                .group_by(WorkflowMembershipRow.workflow_id)
            ).all()
        )
        work_counts: dict[int, dict[WorkStatus, int]] = defaultdict(dict)
        for workflow_id, status, count in self._session.execute(
            select(WorkflowMembershipRow.workflow_id, WorkflowWorkRow.status, func.count())
            .join(WorkflowWorkRow, WorkflowWorkRow.membership_id == WorkflowMembershipRow.id)
            .where(WorkflowMembershipRow.workflow_id.in_(ids), WorkflowMembershipRow.active.is_(True))
            .group_by(WorkflowMembershipRow.workflow_id, WorkflowWorkRow.status)
        ):
            work_counts[workflow_id][status] = count

        unread_new: dict[int, int] = defaultdict(int)
        unread_changed: dict[int, int] = defaultdict(int)
        for workflow_id, kind, count in self._session.execute(
            select(NotificationRow.workflow_id, NotificationRow.kind, func.count())
            .outerjoin(
                NotificationReadRow,
                (NotificationReadRow.notification_id == NotificationRow.id)
                & (NotificationReadRow.user_id == user_id),
            )
            .where(NotificationReadRow.notification_id.is_(None), NotificationRow.workflow_id.in_(ids))
            .group_by(NotificationRow.workflow_id, NotificationRow.kind)
        ):
            if kind is NotificationKind.WORKFLOW_ITEM_CHANGED:
                unread_changed[workflow_id] += count
            else:
                unread_new[workflow_id] += count

        latest_run_ids = dict(
            self._session.execute(
                select(WorkflowPollRunRow.workflow_id, func.max(WorkflowPollRunRow.id))
                .where(WorkflowPollRunRow.workflow_id.in_(ids))
                .group_by(WorkflowPollRunRow.workflow_id)
            ).all()
        )
        runs = {
            run.workflow_id: run
            for run in self._session.execute(
                select(WorkflowPollRunRow).where(
                    WorkflowPollRunRow.id.in_(list(latest_run_ids.values()) or [0])
                )
            ).scalars()
        }

        stats = []
        for workflow in workflows:
            run = runs.get(workflow.id)
            stats.append(
                WorkflowStat(
                    workflow_id=workflow.id,
                    key=workflow.key,
                    name=workflow.name,
                    category=workflow.category,
                    route=workflow.route,
                    view_id=workflow.view_id,
                    sort_order=workflow.sort_order,
                    enabled=workflow.enabled,
                    notification_class=workflow.notification_class,
                    rules_status=workflow.rules_status,
                    baseline_completed_at=workflow.baseline_completed_at,
                    last_poll_at=workflow.last_poll_at,
                    last_success_at=workflow.last_success_at,
                    last_poll_status=workflow.last_poll_status,
                    last_error=workflow.last_error,
                    active_count=active_counts.get(workflow.id, 0),
                    unread_new=unread_new.get(workflow.id, 0),
                    unread_changed=unread_changed.get(workflow.id, 0),
                    by_work_status=work_counts.get(workflow.id, {}),
                    last_run_rows=run.rows_seen if run else None,
                    last_run_created=run.created_count if run else None,
                    last_run_returned=run.returned_count if run else None,
                    last_run_changed=run.changed_count if run else None,
                    last_run_left=run.left_count if run else None,
                    last_run_details_failed=run.details_failed if run else None,
                    last_run_baseline=run.baseline if run else None,
                )
            )
        return stats

    # --- events --------------------------------------------------------------------------------

    def events(
        self,
        *,
        limit: int = 30,
        workflow_ids: list[int] | None = None,
        dossier_id: int | None = None,
        classes: tuple[NotificationClass, ...] | None = None,
        operational: bool = False,
    ) -> list[EventView]:
        statement = (
            select(WorkflowEventRow, WorkflowRow.key, WorkflowRow.name, DossierRow)
            .outerjoin(WorkflowRow, WorkflowRow.id == WorkflowEventRow.workflow_id)
            .outerjoin(DossierRow, DossierRow.id == WorkflowEventRow.dossier_id)
        )
        operational_kinds = list(OPERATIONAL_EVENT_KINDS)
        if operational:
            statement = statement.where(WorkflowEventRow.kind.in_(operational_kinds))
        else:
            statement = statement.where(WorkflowEventRow.kind.notin_(operational_kinds))
        if workflow_ids is not None:
            statement = statement.where(WorkflowEventRow.workflow_id.in_(workflow_ids))
        if dossier_id is not None:
            statement = statement.where(WorkflowEventRow.dossier_id == dossier_id)
        if classes is not None:
            statement = statement.where(WorkflowEventRow.notification_class.in_(classes))
        statement = statement.order_by(
            WorkflowEventRow.detected_at.desc(), WorkflowEventRow.id.desc()
        ).limit(limit)
        views = []
        for event, key, name, dossier in self._session.execute(statement).all():
            views.append(
                EventView(
                    id=event.id,
                    kind=event.kind.value,
                    notification_class=event.notification_class,
                    detected_at=event.detected_at,
                    workflow_id=event.workflow_id,
                    workflow_key=key,
                    workflow_name=name,
                    dossier_id=event.dossier_id,
                    dossier_number=dossier.dossier_number if dossier else None,
                    insured_name=dossier.insured_name if dossier else None,
                    occurrence_id=event.occurrence_id,
                    membership_id=event.membership_id,
                    changed_fields=tuple(json.loads(event.changed_fields_json or "[]")),
                    message=event.message,
                )
            )
        return views

    # --- search ---------------------------------------------------------------------------------

    def search_dossiers(self, needle: str, limit: int = 25) -> list[DossierHit]:
        pattern = f"%{needle.strip().lower()}%"
        rows = self._session.execute(
            select(DossierRow)
            .where(
                or_(
                    func.lower(DossierRow.dossier_number).like(pattern),
                    func.lower(DossierRow.insured_name).like(pattern),
                    func.lower(DossierRow.registration).like(pattern),
                    func.lower(DossierRow.garage).like(pattern),
                )
            )
            .order_by(DossierRow.last_seen_at.desc())
            .limit(limit)
        ).scalars().all()
        keys: dict[int, list[str]] = defaultdict(list)
        if rows:
            for dossier_id, key in self._session.execute(
                select(WorkflowMembershipRow.dossier_id, WorkflowRow.key)
                .join(WorkflowRow, WorkflowRow.id == WorkflowMembershipRow.workflow_id)
                .where(
                    WorkflowMembershipRow.dossier_id.in_([r.id for r in rows]),
                    WorkflowMembershipRow.active.is_(True),
                )
                .order_by(WorkflowRow.sort_order)
            ):
                keys[dossier_id].append(key)
        return [
            DossierHit(
                dossier_id=r.id,
                record_id=r.record_id,
                dossier_number=r.dossier_number,
                insured_name=r.insured_name,
                registration=r.registration,
                garage=r.garage,
                portal_status=r.portal_status,
                active=r.active,
                active_workflow_keys=tuple(keys.get(r.id, ())),
            )
            for r in rows
        ]
