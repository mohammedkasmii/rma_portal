"""RMA Platform V2: occurrences, workflow work, sync runs, events, outbox, AI audit

Revision ID: d41a7c9e5b21
Revises: 8f3b2a1d7c40
Create Date: 2026-09-25

Additive migration. Nothing is dropped: ``dossier_work`` and ``poll_runs`` stay
in place as read-only history for the Garage agréé installation, and every new
column on an existing table is nullable or has a server default.

Backfill (Garage agréé only -- the one workflow that existed before V2):

* every existing membership gets its immutable occurrence history, one
  occurrence per legacy notification plus an initial BASELINE occurrence when
  the dossier was first seen without one;
* every legacy notification is linked to its occurrence and workflow and its
  kind is rewritten to ``WORKFLOW_ITEM_NEW``; ``notification_reads`` are kept
  untouched so per-employee read state survives;
* the dossier-level work status moves to ``workflow_work`` keyed by the Garage
  membership (dossiers without a legacy work row start at ``TO_DO``);
* ``poll_runs`` history is mirrored into ``sync_runs``/``workflow_poll_runs``;
* the workflow row receives the V2 definition metadata and its rules become
  ``CONFIRMED`` (this queue was already validated by the agency in production).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "d41a7c9e5b21"
down_revision: str | None = "8f3b2a1d7c40"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

GARAGE_KEY = "agreement_garage"
GARAGE_PROCEDURE_VALUE = "5ed644a2faf17c0015d8c367"
_BASELINE_TOLERANCE = timedelta(minutes=1)

_TZ = sa.DateTime(timezone=True)

_workflows = sa.table(
    "workflows",
    sa.column("id", sa.Integer),
    sa.column("portal_account_id", sa.Integer),
    sa.column("key", sa.String),
    sa.column("rules_status", sa.String),
    sa.column("notification_class", sa.String),
    sa.column("catalog_version", sa.Integer),
    sa.column("filter_field", sa.String),
    sa.column("filter_operator", sa.String),
    sa.column("filter_value", sa.String),
    sa.column("filter_label", sa.String),
    sa.column("primary_date_key", sa.String),
    sa.column("last_poll_status", sa.String),
    sa.column("last_poll_at", _TZ),
)
_dossiers = sa.table(
    "dossiers",
    sa.column("id", sa.Integer),
    sa.column("portal_account_id", sa.Integer),
    sa.column("first_seen_at", _TZ),
    sa.column("last_seen_at", _TZ),
    sa.column("active", sa.Boolean),
    sa.column("missing_complete_polls", sa.Integer),
)
_memberships = sa.table(
    "workflow_memberships",
    sa.column("id", sa.Integer),
    sa.column("workflow_id", sa.Integer),
    sa.column("dossier_id", sa.Integer),
    sa.column("first_seen_at", _TZ),
    sa.column("last_seen_at", _TZ),
    sa.column("active", sa.Boolean),
    sa.column("missing_complete_polls", sa.Integer),
    sa.column("occurrence_number", sa.Integer),
    sa.column("captured_fields_json", sa.Text),
    sa.column("fingerprint", sa.String),
)
_occurrences = sa.table(
    "workflow_occurrences",
    sa.column("id", sa.Integer),
    sa.column("membership_id", sa.Integer),
    sa.column("workflow_id", sa.Integer),
    sa.column("dossier_id", sa.Integer),
    sa.column("occurrence_number", sa.Integer),
    sa.column("origin", sa.String),
    sa.column("detected_at", _TZ),
)
_notifications = sa.table(
    "notifications",
    sa.column("id", sa.Integer),
    sa.column("dossier_id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("detected_at", _TZ),
    sa.column("workflow_id", sa.Integer),
    sa.column("workflow_occurrence_id", sa.Integer),
)
_dossier_work = sa.table(
    "dossier_work",
    sa.column("dossier_id", sa.Integer),
    sa.column("status", sa.String),
    sa.column("version", sa.Integer),
    sa.column("updated_by", sa.Integer),
    sa.column("updated_at", _TZ),
)
_workflow_work = sa.table(
    "workflow_work",
    sa.column("membership_id", sa.Integer),
    sa.column("status", sa.String),
    sa.column("version", sa.Integer),
    sa.column("updated_by", sa.Integer),
    sa.column("updated_at", _TZ),
)
_poll_runs = sa.table(
    "poll_runs",
    sa.column("id", sa.Integer),
    sa.column("portal_account_id", sa.Integer),
    sa.column("started_at", _TZ),
    sa.column("completed_at", _TZ),
    sa.column("status", sa.String),
    sa.column("rows_seen", sa.Integer),
    sa.column("pages_seen", sa.Integer),
    sa.column("details_failed", sa.Integer),
    sa.column("error", sa.String),
)
_sync_runs = sa.table(
    "sync_runs",
    sa.column("id", sa.Integer),
    sa.column("portal_account_id", sa.Integer),
    sa.column("trigger", sa.String),
    sa.column("started_at", _TZ),
    sa.column("completed_at", _TZ),
    sa.column("status", sa.String),
    sa.column("workflows_total", sa.Integer),
    sa.column("workflows_complete", sa.Integer),
    sa.column("workflows_partial", sa.Integer),
    sa.column("workflows_auth_required", sa.Integer),
    sa.column("workflows_failed", sa.Integer),
    sa.column("error", sa.String),
)
_workflow_poll_runs = sa.table(
    "workflow_poll_runs",
    sa.column("sync_run_id", sa.Integer),
    sa.column("workflow_id", sa.Integer),
    sa.column("started_at", _TZ),
    sa.column("completed_at", _TZ),
    sa.column("status", sa.String),
    sa.column("baseline", sa.Boolean),
    sa.column("rows_seen", sa.Integer),
    sa.column("pages_seen", sa.Integer),
    sa.column("details_failed", sa.Integer),
    sa.column("created_count", sa.Integer),
    sa.column("returned_count", sa.Integer),
    sa.column("changed_count", sa.Integer),
    sa.column("left_count", sa.Integer),
    sa.column("notifications_created", sa.Integer),
    sa.column("error", sa.String),
)


def upgrade() -> None:
    _add_columns()
    _create_tables()
    _add_reference_columns()
    _backfill_garage()
    _create_immutability_triggers()


# --- schema -------------------------------------------------------------------------------------


def _add_columns() -> None:
    op.add_column(
        "workflows",
        sa.Column("notification_class", sa.String(20), nullable=False, server_default="ACTION"),
    )
    op.add_column(
        "workflows",
        sa.Column("catalog_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("workflows", sa.Column("filter_field", sa.String(50), nullable=True))
    op.add_column("workflows", sa.Column("filter_operator", sa.String(20), nullable=True))
    op.add_column("workflows", sa.Column("filter_value", sa.String(100), nullable=True))
    op.add_column("workflows", sa.Column("filter_label", sa.String(200), nullable=True))
    op.add_column("workflows", sa.Column("primary_date_key", sa.String(100), nullable=True))
    op.add_column("workflows", sa.Column("last_poll_status", sa.String(20), nullable=True))

    op.add_column(
        "dossiers",
        sa.Column("detail_fields_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column("dossiers", sa.Column("detail_fetched_at", _TZ, nullable=True))


def _create_tables() -> None:
    op.create_table(
        "workflow_occurrences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("membership_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("dossier_id", sa.Integer(), nullable=False),
        sa.Column("occurrence_number", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("detected_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["workflow_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dossier_id"], ["dossiers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id", "occurrence_number", name="uq_occurrence_membership_number"
        ),
    )
    op.create_index(
        "ix_occurrence_workflow_dossier", "workflow_occurrences", ["workflow_id", "dossier_id"]
    )
    op.create_index("ix_workflow_occurrences_dossier_id", "workflow_occurrences", ["dossier_id"])

    op.create_table(
        "workflow_work",
        sa.Column("membership_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["workflow_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("membership_id"),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portal_account_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("completed_at", _TZ, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("workflows_total", sa.Integer(), nullable=False),
        sa.Column("workflows_complete", sa.Integer(), nullable=False),
        sa.Column("workflows_partial", sa.Integer(), nullable=False),
        sa.Column("workflows_auth_required", sa.Integer(), nullable=False),
        sa.Column("workflows_failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.ForeignKeyConstraint(["portal_account_id"], ["portal_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_portal_account_id", "sync_runs", ["portal_account_id"])

    op.create_table(
        "workflow_poll_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("completed_at", _TZ, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("baseline", sa.Boolean(), nullable=False),
        sa.Column("rows_seen", sa.Integer(), nullable=False),
        sa.Column("pages_seen", sa.Integer(), nullable=False),
        sa.Column("details_failed", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("returned_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("left_count", sa.Integer(), nullable=False),
        sa.Column("notifications_created", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_poll_runs_sync_run_id", "workflow_poll_runs", ["sync_run_id"])
    op.create_index(
        "ix_workflow_poll_run_workflow", "workflow_poll_runs", ["workflow_id", "started_at"]
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=True),
        sa.Column("membership_id", sa.Integer(), nullable=True),
        sa.Column("occurrence_id", sa.Integer(), nullable=True),
        sa.Column("dossier_id", sa.Integer(), nullable=True),
        sa.Column("poll_run_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("notification_class", sa.String(20), nullable=False),
        sa.Column("detected_at", _TZ, nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("before_fingerprints_json", sa.Text(), nullable=False),
        sa.Column("after_fingerprints_json", sa.Text(), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["workflow_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["occurrence_id"], ["workflow_occurrences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dossier_id"], ["dossiers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["poll_run_id"], ["workflow_poll_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_event_workflow_time", "workflow_events", ["workflow_id", "detected_at"]
    )
    op.create_index(
        "ix_workflow_event_dossier_time", "workflow_events", ["dossier_id", "detected_at"]
    )

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(60), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("available_at", _TZ, nullable=False),
        sa.Column("processed_at", _TZ, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_pending", "outbox_messages", ["processed_at", "available_at"])

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("subject_type", sa.String(30), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(30), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_run_subject", "ai_runs", ["subject_type", "subject_id"])


def _add_reference_columns() -> None:
    # Plain nullable columns: SQLite cannot ADD CONSTRAINT, and the ORM/PostgreSQL
    # schema created by this same migration carries the foreign keys through
    # batch mode where the dialect needs it.
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("workflow_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("workflow_occurrence_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("workflow_event_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notification_workflow", "workflows", ["workflow_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_notification_occurrence",
            "workflow_occurrences",
            ["workflow_occurrence_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_notification_event",
            "workflow_events",
            ["workflow_event_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_notifications_workflow_id", ["workflow_id"])
        batch.create_index("ix_notifications_workflow_occurrence_id", ["workflow_occurrence_id"])

    with op.batch_alter_table("dossier_notes") as batch:
        batch.add_column(sa.Column("workflow_membership_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_note_membership",
            "workflow_memberships",
            ["workflow_membership_id"],
            ["id"],
            ondelete="SET NULL",
        )


# --- backfill -----------------------------------------------------------------------------------


def _backfill_garage() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.update(_workflows)
        .where(_workflows.c.key == GARAGE_KEY)
        .values(
            rules_status="CONFIRMED",
            notification_class="ACTION",
            catalog_version=1,
            filter_field="field_219",
            filter_operator="is",
            filter_value=GARAGE_PROCEDURE_VALUE,
            filter_label="Garage agréé",
            primary_date_key="date_envoi_devis_garage",
        )
    )

    garage_ids = [
        row.id for row in bind.execute(sa.select(_workflows.c.id).where(_workflows.c.key == GARAGE_KEY))
    ]
    if not garage_ids:
        return

    _sync_garage_memberships(bind)
    _backfill_occurrences_and_notifications(bind, garage_ids)
    _backfill_work_status(bind, garage_ids)
    _backfill_poll_history(bind)


def _sync_garage_memberships(bind: sa.Connection) -> None:
    """Make every Garage agréé dossier's membership match its lifecycle state.

    The pre-V2 synchronizer only maintained ``dossiers``: dossiers detected after
    the 8f3b2a1d7c40 foundation migration never received a membership, and the
    memberships that did exist stopped following their dossier. The dossier is
    the authoritative source for that queue's state until this migration.
    """
    for workflow in bind.execute(
        sa.select(_workflows.c.id, _workflows.c.portal_account_id).where(_workflows.c.key == GARAGE_KEY)
    ).all():
        by_dossier = {
            row.dossier_id: row
            for row in bind.execute(
                sa.select(_memberships.c.id, _memberships.c.dossier_id).where(
                    _memberships.c.workflow_id == workflow.id
                )
            )
        }
        for dossier in bind.execute(
            sa.select(
                _dossiers.c.id,
                _dossiers.c.first_seen_at,
                _dossiers.c.last_seen_at,
                _dossiers.c.active,
                _dossiers.c.missing_complete_polls,
            ).where(_dossiers.c.portal_account_id == workflow.portal_account_id)
        ):
            existing = by_dossier.get(dossier.id)
            if existing is None:
                bind.execute(
                    sa.insert(_memberships).values(
                        workflow_id=workflow.id,
                        dossier_id=dossier.id,
                        first_seen_at=dossier.first_seen_at,
                        last_seen_at=dossier.last_seen_at,
                        active=dossier.active,
                        missing_complete_polls=dossier.missing_complete_polls,
                        occurrence_number=1,
                        captured_fields_json="{}",
                        fingerprint="",
                    )
                )
            else:
                bind.execute(
                    sa.update(_memberships)
                    .where(_memberships.c.id == existing.id)
                    .values(
                        last_seen_at=dossier.last_seen_at,
                        active=dossier.active,
                        missing_complete_polls=dossier.missing_complete_polls,
                    )
                )


def _backfill_occurrences_and_notifications(bind: sa.Connection, garage_ids: list[int]) -> None:
    memberships = bind.execute(
        sa.select(
            _memberships.c.id,
            _memberships.c.workflow_id,
            _memberships.c.dossier_id,
            _memberships.c.first_seen_at,
        )
        .where(_memberships.c.workflow_id.in_(garage_ids))
        .order_by(_memberships.c.id)
    ).all()

    notifications_by_dossier: dict[int, list] = defaultdict(list)
    for note in bind.execute(
        sa.select(_notifications.c.id, _notifications.c.dossier_id, _notifications.c.detected_at).order_by(
            _notifications.c.detected_at, _notifications.c.id
        )
    ):
        notifications_by_dossier[note.dossier_id].append(note)

    for membership in memberships:
        notes = notifications_by_dossier.get(membership.dossier_id, [])
        # (occurrence number, origin, detected_at, notification ids)
        plan: list[tuple[int, str, object, list[int]]] = []
        first_note = notes[0] if notes else None
        first_is_initial = first_note is not None and (
            abs(first_note.detected_at - membership.first_seen_at) <= _BASELINE_TOLERANCE
        )
        if first_is_initial:
            plan.append((1, "NEW", first_note.detected_at, [first_note.id]))
            remaining = notes[1:]
        else:
            # The dossier was in the queue before any alert existed: it belongs to the
            # baseline, and every later legacy alert is a reappearance.
            plan.append((1, "BASELINE", membership.first_seen_at, []))
            remaining = notes
        for note in remaining:
            plan.append((len(plan) + 1, "RETURNED", note.detected_at, [note.id]))

        for number, origin, detected_at, note_ids in plan:
            occurrence_id = bind.execute(
                sa.insert(_occurrences)
                .values(
                    membership_id=membership.id,
                    workflow_id=membership.workflow_id,
                    dossier_id=membership.dossier_id,
                    occurrence_number=number,
                    origin=origin,
                    detected_at=detected_at,
                )
                .returning(_occurrences.c.id)
            ).scalar_one()
            for note_id in note_ids:
                bind.execute(
                    sa.update(_notifications)
                    .where(_notifications.c.id == note_id)
                    .values(
                        workflow_id=membership.workflow_id,
                        workflow_occurrence_id=occurrence_id,
                        kind="WORKFLOW_ITEM_NEW" if origin != "RETURNED" else "WORKFLOW_ITEM_RETURNED",
                    )
                )
        bind.execute(
            sa.update(_memberships)
            .where(_memberships.c.id == membership.id)
            .values(occurrence_number=len(plan))
        )


def _backfill_work_status(bind: sa.Connection, garage_ids: list[int]) -> None:
    work_by_dossier = {
        row.dossier_id: row for row in bind.execute(sa.select(_dossier_work))
    }
    memberships = bind.execute(
        sa.select(_memberships.c.id, _memberships.c.dossier_id, _memberships.c.first_seen_at).where(
            _memberships.c.workflow_id.in_(garage_ids)
        )
    ).all()
    for membership in memberships:
        legacy = work_by_dossier.get(membership.dossier_id)
        bind.execute(
            sa.insert(_workflow_work).values(
                membership_id=membership.id,
                status=legacy.status if legacy else "TO_DO",
                version=legacy.version if legacy else 1,
                updated_by=legacy.updated_by if legacy else None,
                updated_at=legacy.updated_at if legacy else membership.first_seen_at,
            )
        )


def _backfill_poll_history(bind: sa.Connection) -> None:
    for workflow in bind.execute(
        sa.select(_workflows.c.id, _workflows.c.portal_account_id).where(_workflows.c.key == GARAGE_KEY)
    ).all():
        runs = bind.execute(
            sa.select(_poll_runs)
            .where(_poll_runs.c.portal_account_id == workflow.portal_account_id)
            .order_by(_poll_runs.c.id)
        ).all()
        last_status = None
        for run in runs:
            counts = {
                "COMPLETE": 0,
                "PARTIAL": 0,
                "AUTH_REQUIRED": 0,
                "FAILED": 0,
            }
            counts[run.status] = 1
            sync_run_id = bind.execute(
                sa.insert(_sync_runs)
                .values(
                    portal_account_id=run.portal_account_id,
                    trigger="SCHEDULED",
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    status=run.status,
                    workflows_total=1,
                    workflows_complete=counts["COMPLETE"],
                    workflows_partial=counts["PARTIAL"],
                    workflows_auth_required=counts["AUTH_REQUIRED"],
                    workflows_failed=counts["FAILED"],
                    error=run.error,
                )
                .returning(_sync_runs.c.id)
            ).scalar_one()
            bind.execute(
                sa.insert(_workflow_poll_runs).values(
                    sync_run_id=sync_run_id,
                    workflow_id=workflow.id,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    status=run.status,
                    baseline=False,
                    rows_seen=run.rows_seen,
                    pages_seen=run.pages_seen,
                    details_failed=run.details_failed,
                    created_count=0,
                    returned_count=0,
                    changed_count=0,
                    left_count=0,
                    notifications_created=0,
                    error=run.error,
                )
            )
            last_status = run.status
        if last_status is not None:
            bind.execute(
                sa.update(_workflows)
                .where(_workflows.c.id == workflow.id)
                .values(last_poll_status=last_status)
            )


# --- immutability -------------------------------------------------------------------------------

_IMMUTABLE_TABLES = ("workflow_occurrences", "workflow_events")


def _create_immutability_triggers() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        for table in _IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
            )
    elif dialect == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION rma_reject_update() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END; "
            "$$ LANGUAGE plpgsql"
        )
        for table in _IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION rma_reject_update()"
            )


def _drop_immutability_triggers() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        return
    for table in _IMMUTABLE_TABLES:
        if dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS rma_reject_update()")


def downgrade() -> None:
    _drop_immutability_triggers()
    with op.batch_alter_table("dossier_notes") as batch:
        batch.drop_constraint("fk_note_membership", type_="foreignkey")
        batch.drop_column("workflow_membership_id")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_index("ix_notifications_workflow_occurrence_id")
        batch.drop_index("ix_notifications_workflow_id")
        batch.drop_constraint("fk_notification_event", type_="foreignkey")
        batch.drop_constraint("fk_notification_occurrence", type_="foreignkey")
        batch.drop_constraint("fk_notification_workflow", type_="foreignkey")
        batch.drop_column("workflow_event_id")
        batch.drop_column("workflow_occurrence_id")
        batch.drop_column("workflow_id")

    op.drop_index("ix_ai_run_subject", table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_index("ix_outbox_pending", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_workflow_event_dossier_time", table_name="workflow_events")
    op.drop_index("ix_workflow_event_workflow_time", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index("ix_workflow_poll_run_workflow", table_name="workflow_poll_runs")
    op.drop_index("ix_workflow_poll_runs_sync_run_id", table_name="workflow_poll_runs")
    op.drop_table("workflow_poll_runs")
    op.drop_index("ix_sync_runs_portal_account_id", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_table("workflow_work")
    op.drop_index("ix_workflow_occurrences_dossier_id", table_name="workflow_occurrences")
    op.drop_index("ix_occurrence_workflow_dossier", table_name="workflow_occurrences")
    op.drop_table("workflow_occurrences")

    op.drop_column("dossiers", "detail_fetched_at")
    op.drop_column("dossiers", "detail_fields_json")
    for column in (
        "last_poll_status",
        "primary_date_key",
        "filter_label",
        "filter_value",
        "filter_operator",
        "filter_field",
        "catalog_version",
        "notification_class",
    ):
        op.drop_column("workflows", column)
