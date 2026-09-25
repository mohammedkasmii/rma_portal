"""SQLAlchemy repositories for the V2 workflow tables.

Kept apart from ``repositories.py`` (the pre-V2 dossier/user repositories) so
each module stays small. Everything returns ``domain.models`` objects; ORM
rows never leave ``infrastructure.db``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rma_portal.domain.enums import (
    AiFeature,
    NotificationClass,
    OccurrenceOrigin,
    PollStatus,
    SyncTrigger,
    WorkStatus,
)
from rma_portal.domain.models import (
    AiRun,
    OutboxMessage,
    SyncRun,
    WorkflowEvent,
    WorkflowOccurrence,
    WorkflowPollRun,
    WorkflowWork,
    WorkStatusConflict,
)
from rma_portal.infrastructure.db.models import (
    AiRunRow,
    OutboxMessageRow,
    SyncRunRow,
    WorkflowEventRow,
    WorkflowMembershipRow,
    WorkflowOccurrenceRow,
    WorkflowPollRunRow,
    WorkflowWorkRow,
)

OUTBOX_MAX_ATTEMPTS = 8


def _occurrence(row: WorkflowOccurrenceRow) -> WorkflowOccurrence:
    return WorkflowOccurrence(
        id=row.id,
        membership_id=row.membership_id,
        workflow_id=row.workflow_id,
        dossier_id=row.dossier_id,
        occurrence_number=row.occurrence_number,
        origin=row.origin,
        detected_at=row.detected_at,
    )


def _work(row: WorkflowWorkRow) -> WorkflowWork:
    return WorkflowWork(
        membership_id=row.membership_id,
        status=row.status,
        version=row.version,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _sync_run(row: SyncRunRow) -> SyncRun:
    return SyncRun(
        id=row.id,
        portal_account_id=row.portal_account_id,
        trigger=row.trigger,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        workflows_total=row.workflows_total,
        workflows_complete=row.workflows_complete,
        workflows_partial=row.workflows_partial,
        workflows_auth_required=row.workflows_auth_required,
        workflows_failed=row.workflows_failed,
        error=row.error,
    )


def _poll_run(row: WorkflowPollRunRow) -> WorkflowPollRun:
    return WorkflowPollRun(
        id=row.id,
        sync_run_id=row.sync_run_id,
        workflow_id=row.workflow_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        baseline=row.baseline,
        rows_seen=row.rows_seen,
        pages_seen=row.pages_seen,
        details_failed=row.details_failed,
        created_count=row.created_count,
        returned_count=row.returned_count,
        changed_count=row.changed_count,
        left_count=row.left_count,
        notifications_created=row.notifications_created,
        error=row.error,
    )


def _event(row: WorkflowEventRow) -> WorkflowEvent:
    return WorkflowEvent(
        id=row.id,
        kind=row.kind,
        detected_at=row.detected_at,
        notification_class=row.notification_class,
        workflow_id=row.workflow_id,
        membership_id=row.membership_id,
        occurrence_id=row.occurrence_id,
        dossier_id=row.dossier_id,
        poll_run_id=row.poll_run_id,
        changed_fields=tuple(json.loads(row.changed_fields_json)),
        before_fingerprints=json.loads(row.before_fingerprints_json),
        after_fingerprints=json.loads(row.after_fingerprints_json),
        message=row.message,
    )


def _outbox(row: OutboxMessageRow) -> OutboxMessage:
    return OutboxMessage(
        id=row.id,
        topic=row.topic,
        payload=json.loads(row.payload_json),
        created_at=row.created_at,
        available_at=row.available_at,
        processed_at=row.processed_at,
        attempts=row.attempts,
        last_error=row.last_error,
    )


def _ai_run(row: AiRunRow) -> AiRun:
    return AiRun(
        id=row.id,
        feature=row.feature,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        model=row.model,
        prompt_version=row.prompt_version,
        context_hash=row.context_hash,
        status=row.status,
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        result=json.loads(row.result_json) if row.result_json else None,
        error=row.error,
        created_by=row.created_by,
    )


class SqlAlchemyWorkflowOccurrenceRepository:
    """Insert-only: there is deliberately no update method."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        membership_id: int,
        workflow_id: int,
        dossier_id: int,
        occurrence_number: int,
        origin: OccurrenceOrigin,
        detected_at: datetime,
    ) -> WorkflowOccurrence:
        row = WorkflowOccurrenceRow(
            membership_id=membership_id,
            workflow_id=workflow_id,
            dossier_id=dossier_id,
            occurrence_number=occurrence_number,
            origin=origin,
            detected_at=detected_at,
        )
        self._session.add(row)
        self._session.flush()
        return _occurrence(row)

    def get(self, occurrence_id: int) -> WorkflowOccurrence | None:
        row = self._session.get(WorkflowOccurrenceRow, occurrence_id)
        return _occurrence(row) if row else None

    def list_for_membership(self, membership_id: int) -> list[WorkflowOccurrence]:
        rows = self._session.execute(
            select(WorkflowOccurrenceRow)
            .where(WorkflowOccurrenceRow.membership_id == membership_id)
            .order_by(WorkflowOccurrenceRow.occurrence_number)
        ).scalars()
        return [_occurrence(row) for row in rows]

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowOccurrence]:
        rows = self._session.execute(
            select(WorkflowOccurrenceRow)
            .where(WorkflowOccurrenceRow.dossier_id == dossier_id)
            .order_by(WorkflowOccurrenceRow.detected_at, WorkflowOccurrenceRow.id)
        ).scalars()
        return [_occurrence(row) for row in rows]

    def latest_for_membership(self, membership_id: int) -> WorkflowOccurrence | None:
        row = self._session.execute(
            select(WorkflowOccurrenceRow)
            .where(WorkflowOccurrenceRow.membership_id == membership_id)
            .order_by(WorkflowOccurrenceRow.occurrence_number.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _occurrence(row) if row else None


class SqlAlchemyWorkflowWorkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, membership_id: int) -> WorkflowWork | None:
        row = self._session.get(WorkflowWorkRow, membership_id)
        return _work(row) if row else None

    def create_initial(self, membership_id: int, now: datetime) -> WorkflowWork:
        row = self._session.get(WorkflowWorkRow, membership_id)
        if row is None:
            row = WorkflowWorkRow(
                membership_id=membership_id,
                status=WorkStatus.TO_DO,
                version=1,
                updated_by=None,
                updated_at=now,
            )
            self._session.add(row)
            self._session.flush()
        return _work(row)

    def upsert(
        self,
        membership_id: int,
        status: WorkStatus,
        expected_version: int | None,
        updated_by: int,
        updated_at: datetime,
    ) -> WorkflowWork:
        row = self._session.get(WorkflowWorkRow, membership_id)
        if row is None:
            row = WorkflowWorkRow(
                membership_id=membership_id,
                status=status,
                version=1,
                updated_by=updated_by,
                updated_at=updated_at,
            )
            self._session.add(row)
            self._session.flush()
            return _work(row)
        if expected_version is not None and expected_version != row.version:
            raise WorkStatusConflict(membership_id, expected_version, row.version)
        row.status = status
        row.version += 1
        row.updated_by = updated_by
        row.updated_at = updated_at
        return _work(row)

    def counts_by_workflow(self, workflow_ids: list[int]) -> dict[tuple[int, WorkStatus], int]:
        """Active memberships per (workflow, work status)."""
        self._session.flush()
        rows = self._session.execute(
            select(WorkflowMembershipRow.workflow_id, WorkflowWorkRow.status, func.count())
            .join(WorkflowWorkRow, WorkflowWorkRow.membership_id == WorkflowMembershipRow.id)
            .where(
                WorkflowMembershipRow.workflow_id.in_(workflow_ids),
                WorkflowMembershipRow.active.is_(True),
            )
            .group_by(WorkflowMembershipRow.workflow_id, WorkflowWorkRow.status)
        ).all()
        return {(workflow_id, status): count for workflow_id, status, count in rows}


class SqlAlchemySyncRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, account_id: int, trigger: SyncTrigger, started_at: datetime) -> SyncRun:
        row = SyncRunRow(
            portal_account_id=account_id,
            trigger=trigger,
            started_at=started_at,
            status=PollStatus.FAILED,
        )
        self._session.add(row)
        self._session.flush()
        return _sync_run(row)

    def finish(
        self,
        sync_run_id: int,
        *,
        status: PollStatus,
        completed_at: datetime,
        workflows_total: int,
        workflows_complete: int,
        workflows_partial: int,
        workflows_auth_required: int,
        workflows_failed: int,
        error: str | None,
    ) -> SyncRun:
        row = self._session.get(SyncRunRow, sync_run_id)
        if row is None:
            raise LookupError(f"sync run {sync_run_id} not found")
        row.status = status
        row.completed_at = completed_at
        row.workflows_total = workflows_total
        row.workflows_complete = workflows_complete
        row.workflows_partial = workflows_partial
        row.workflows_auth_required = workflows_auth_required
        row.workflows_failed = workflows_failed
        row.error = error
        return _sync_run(row)

    def latest(self, account_id: int) -> SyncRun | None:
        row = self._session.execute(
            select(SyncRunRow)
            .where(SyncRunRow.portal_account_id == account_id)
            .order_by(SyncRunRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _sync_run(row) if row else None

    def recent(self, account_id: int, limit: int = 20) -> list[SyncRun]:
        rows = self._session.execute(
            select(SyncRunRow)
            .where(SyncRunRow.portal_account_id == account_id)
            .order_by(SyncRunRow.id.desc())
            .limit(limit)
        ).scalars()
        return [_sync_run(row) for row in rows]


class SqlAlchemyWorkflowPollRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(
        self, sync_run_id: int, workflow_id: int, started_at: datetime, *, baseline: bool
    ) -> WorkflowPollRun:
        row = WorkflowPollRunRow(
            sync_run_id=sync_run_id,
            workflow_id=workflow_id,
            started_at=started_at,
            status=PollStatus.FAILED,
            baseline=baseline,
        )
        self._session.add(row)
        self._session.flush()
        return _poll_run(row)

    def finish(
        self,
        poll_run_id: int,
        *,
        status: PollStatus,
        completed_at: datetime,
        rows_seen: int,
        pages_seen: int,
        details_failed: int,
        created_count: int,
        returned_count: int,
        changed_count: int,
        left_count: int,
        notifications_created: int,
        error: str | None,
        baseline: bool | None = None,
    ) -> WorkflowPollRun:
        row = self._session.get(WorkflowPollRunRow, poll_run_id)
        if row is None:
            raise LookupError(f"workflow poll run {poll_run_id} not found")
        row.status = status
        row.completed_at = completed_at
        row.rows_seen = rows_seen
        row.pages_seen = pages_seen
        row.details_failed = details_failed
        row.created_count = created_count
        row.returned_count = returned_count
        row.changed_count = changed_count
        row.left_count = left_count
        row.notifications_created = notifications_created
        row.error = error
        if baseline is not None:
            row.baseline = baseline
        return _poll_run(row)

    def list_for_sync_run(self, sync_run_id: int) -> list[WorkflowPollRun]:
        rows = self._session.execute(
            select(WorkflowPollRunRow)
            .where(WorkflowPollRunRow.sync_run_id == sync_run_id)
            .order_by(WorkflowPollRunRow.id)
        ).scalars()
        return [_poll_run(row) for row in rows]

    def latest_for_workflow(self, workflow_id: int) -> WorkflowPollRun | None:
        row = self._session.execute(
            select(WorkflowPollRunRow)
            .where(WorkflowPollRunRow.workflow_id == workflow_id)
            .order_by(WorkflowPollRunRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _poll_run(row) if row else None

    def recent_for_workflow(self, workflow_id: int, limit: int = 10) -> list[WorkflowPollRun]:
        rows = self._session.execute(
            select(WorkflowPollRunRow)
            .where(WorkflowPollRunRow.workflow_id == workflow_id)
            .order_by(WorkflowPollRunRow.id.desc())
            .limit(limit)
        ).scalars()
        return [_poll_run(row) for row in rows]


class SqlAlchemyWorkflowEventRepository:
    """Insert-only activity feed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: WorkflowEvent) -> WorkflowEvent:
        row = WorkflowEventRow(
            workflow_id=event.workflow_id,
            membership_id=event.membership_id,
            occurrence_id=event.occurrence_id,
            dossier_id=event.dossier_id,
            poll_run_id=event.poll_run_id,
            kind=event.kind,
            notification_class=event.notification_class,
            detected_at=event.detected_at,
            changed_fields_json=json.dumps(list(event.changed_fields)),
            before_fingerprints_json=json.dumps(dict(event.before_fingerprints), sort_keys=True),
            after_fingerprints_json=json.dumps(dict(event.after_fingerprints), sort_keys=True),
            message=event.message,
        )
        self._session.add(row)
        self._session.flush()
        return _event(row)

    def list_for_dossier(self, dossier_id: int) -> list[WorkflowEvent]:
        rows = self._session.execute(
            select(WorkflowEventRow)
            .where(WorkflowEventRow.dossier_id == dossier_id)
            .order_by(WorkflowEventRow.detected_at.desc(), WorkflowEventRow.id.desc())
        ).scalars()
        return [_event(row) for row in rows]

    def list_recent(
        self,
        *,
        limit: int = 50,
        workflow_id: int | None = None,
        classes: tuple[NotificationClass, ...] | None = None,
        only_operational: bool = False,
    ) -> list[WorkflowEvent]:
        from rma_portal.domain.enums import OPERATIONAL_EVENT_KINDS

        statement = select(WorkflowEventRow)
        if workflow_id is not None:
            statement = statement.where(WorkflowEventRow.workflow_id == workflow_id)
        if classes is not None:
            statement = statement.where(WorkflowEventRow.notification_class.in_(classes))
        operational = list(OPERATIONAL_EVENT_KINDS)
        if only_operational:
            statement = statement.where(WorkflowEventRow.kind.in_(operational))
        else:
            statement = statement.where(WorkflowEventRow.kind.notin_(operational))
        rows = self._session.execute(
            statement.order_by(WorkflowEventRow.detected_at.desc(), WorkflowEventRow.id.desc()).limit(
                limit
            )
        ).scalars()
        return [_event(row) for row in rows]

    def latest_operational_for_workflow(self, workflow_id: int | None) -> WorkflowEvent | None:
        from rma_portal.domain.enums import OPERATIONAL_EVENT_KINDS

        statement = select(WorkflowEventRow).where(
            WorkflowEventRow.kind.in_(list(OPERATIONAL_EVENT_KINDS))
        )
        if workflow_id is None:
            statement = statement.where(WorkflowEventRow.workflow_id.is_(None))
        else:
            statement = statement.where(WorkflowEventRow.workflow_id == workflow_id)
        row = self._session.execute(
            statement.order_by(WorkflowEventRow.id.desc()).limit(1)
        ).scalar_one_or_none()
        return _event(row) if row else None


class SqlAlchemyOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        topic: str,
        payload: Mapping[str, object],
        now: datetime,
        *,
        available_at: datetime | None = None,
    ) -> OutboxMessage:
        row = OutboxMessageRow(
            topic=topic,
            payload_json=json.dumps(dict(payload), sort_keys=True),
            created_at=now,
            available_at=available_at or now,
        )
        self._session.add(row)
        self._session.flush()
        return _outbox(row)

    def claim_pending(self, now: datetime, limit: int = 50) -> list[OutboxMessage]:
        rows = self._session.execute(
            select(OutboxMessageRow)
            .where(
                OutboxMessageRow.processed_at.is_(None),
                OutboxMessageRow.available_at <= now,
                OutboxMessageRow.attempts < OUTBOX_MAX_ATTEMPTS,
            )
            .order_by(OutboxMessageRow.id)
            .limit(limit)
        ).scalars()
        return [_outbox(row) for row in rows]

    def mark_processed(self, message_id: int, processed_at: datetime) -> None:
        row = self._session.get(OutboxMessageRow, message_id)
        if row is not None:
            row.processed_at = processed_at
            row.attempts += 1
            row.last_error = None

    def mark_failed(self, message_id: int, error: str, now: datetime) -> None:
        row = self._session.get(OutboxMessageRow, message_id)
        if row is not None:
            row.attempts += 1
            row.last_error = error[:1000]
            # Exponential backoff, capped at one hour.
            row.available_at = now + timedelta(seconds=min(3600, 30 * 2**row.attempts))

    def pending_count(self, topic: str | None = None) -> int:
        statement = select(func.count()).select_from(OutboxMessageRow).where(
            OutboxMessageRow.processed_at.is_(None),
            OutboxMessageRow.attempts < OUTBOX_MAX_ATTEMPTS,
        )
        if topic is not None:
            statement = statement.where(OutboxMessageRow.topic == topic)
        return self._session.execute(statement).scalar_one()

    def has_pending(self, topic: str) -> bool:
        return self.pending_count(topic) > 0


class SqlAlchemyAiRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: AiRun) -> AiRun:
        row = AiRunRow(
            feature=run.feature,
            subject_type=run.subject_type,
            subject_id=run.subject_id,
            model=run.model,
            prompt_version=run.prompt_version,
            context_hash=run.context_hash,
            status=run.status,
            started_at=run.started_at,
            duration_ms=run.duration_ms,
            result_json=(
                json.dumps(dict(run.result), sort_keys=True, ensure_ascii=False)
                if run.result is not None
                else None
            ),
            error=run.error[:500] if run.error else None,
            created_by=run.created_by,
        )
        self._session.add(row)
        self._session.flush()
        return _ai_run(row)

    def latest_successful(
        self, feature: AiFeature, subject_type: str, subject_id: int | None
    ) -> AiRun | None:
        from rma_portal.domain.enums import AiRunStatus

        statement = select(AiRunRow).where(
            AiRunRow.feature == feature,
            AiRunRow.subject_type == subject_type,
            AiRunRow.status == AiRunStatus.SUCCEEDED,
        )
        if subject_id is None:
            statement = statement.where(AiRunRow.subject_id.is_(None))
        else:
            statement = statement.where(AiRunRow.subject_id == subject_id)
        row = self._session.execute(
            statement.order_by(AiRunRow.id.desc()).limit(1)
        ).scalar_one_or_none()
        return _ai_run(row) if row else None

    def recent(self, limit: int = 20) -> list[AiRun]:
        rows = self._session.execute(
            select(AiRunRow).order_by(AiRunRow.id.desc()).limit(limit)
        ).scalars()
        return [_ai_run(row) for row in rows]


__all__ = [
    "OUTBOX_MAX_ATTEMPTS",
    "SqlAlchemyAiRunRepository",
    "SqlAlchemyOutboxRepository",
    "SqlAlchemySyncRunRepository",
    "SqlAlchemyWorkflowEventRepository",
    "SqlAlchemyWorkflowOccurrenceRepository",
    "SqlAlchemyWorkflowPollRunRepository",
    "SqlAlchemyWorkflowWorkRepository",
]
