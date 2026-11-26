"""route confirmed Garage and Hifad agreement statuses

Revision ID: e7b4f92c1a06
Revises: d41a7c9e5b21
Create Date: 2026-11-26

The agency confirmed two membership rules from live aggregate evidence:

* Garage agreement work includes only ``Devis envoyé par le garage``;
* ``Dossier créé`` rows from the Hifad search page belong to the Hifad agreement workflow.

Resetting only the Hifad agreement baseline makes the first read of its new source a silent
baseline, so the existing dossiers are reclassified without a burst of false "new" alerts.
No dossier, membership, event or notification history is deleted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b4f92c1a06"
down_revision: str | None = "d41a7c9e5b21"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    workflows = sa.table(
        "workflows",
        sa.column("key", sa.String),
        sa.column("rules_status", sa.String),
        sa.column("baseline_completed_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        workflows.update()
        .where(workflows.c.key == "agreement_hifad")
        .values(rules_status="CONFIRMED", baseline_completed_at=None)
    )


def downgrade() -> None:
    workflows = sa.table(
        "workflows",
        sa.column("key", sa.String),
        sa.column("rules_status", sa.String),
    )
    op.execute(
        workflows.update()
        .where(workflows.c.key == "agreement_hifad")
        .values(rules_status="CAPTURE_DERIVED")
    )
