"""add multi-workflow foundation

Revision ID: 8f3b2a1d7c40
Revises: c22e2bb290df
Create Date: 2026-09-24

The current Garage agree implementation remains authoritative during the
incremental migration. These additive tables establish workflow identity and
backfill one membership per existing dossier so the later synchronizer cutover
does not lose detection history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8f3b2a1d7c40"
down_revision: str | None = "c22e2bb290df"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portal_account_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=150), nullable=False),
        sa.Column("route", sa.String(length=500), nullable=False),
        sa.Column("view_id", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("rules_status", sa.String(length=20), nullable=False),
        sa.Column("baseline_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["portal_account_id"], ["portal_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portal_account_id", "key", name="uq_workflow_account_key"),
    )
    op.create_index(
        "ix_workflow_account_enabled",
        "workflows",
        ["portal_account_id", "enabled"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO workflows (
                portal_account_id, key, name, category, route, view_id,
                enabled, sort_order, rules_status, baseline_completed_at,
                last_poll_at, last_success_at, last_error
            )
            SELECT
                id,
                'agreement_garage',
                'Instance d''accord - Garage agréé',
                'Dossiers en instance d''accord',
                '#dossiers-en-instance-accord/',
                'view_1874',
                TRUE,
                10,
                'UNCONFIRMED',
                baseline_completed_at,
                last_poll_at,
                last_success_at,
                last_error
            FROM portal_accounts
            """
        )
    )

    op.create_table(
        "workflow_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("dossier_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("missing_complete_polls", sa.Integer(), nullable=False),
        sa.Column("occurrence_number", sa.Integer(), nullable=False),
        sa.Column("captured_fields_json", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dossier_id"], ["dossiers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "dossier_id", name="uq_membership_workflow_dossier"
        ),
    )
    op.create_index(
        "ix_membership_workflow_active",
        "workflow_memberships",
        ["workflow_id", "active"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO workflow_memberships (
                workflow_id, dossier_id, first_seen_at, last_seen_at, active,
                missing_complete_polls, occurrence_number,
                captured_fields_json, fingerprint, last_changed_at
            )
            SELECT
                workflows.id,
                dossiers.id,
                dossiers.first_seen_at,
                dossiers.last_seen_at,
                dossiers.active,
                dossiers.missing_complete_polls,
                1,
                '{}',
                '',
                NULL
            FROM dossiers
            JOIN workflows
              ON workflows.portal_account_id = dossiers.portal_account_id
             AND workflows.key = 'agreement_garage'
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_membership_workflow_active", table_name="workflow_memberships")
    op.drop_table("workflow_memberships")
    op.drop_index("ix_workflow_account_enabled", table_name="workflows")
    op.drop_table("workflows")
