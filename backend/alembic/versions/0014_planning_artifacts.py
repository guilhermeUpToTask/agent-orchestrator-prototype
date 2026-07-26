"""Durable enrichment progress across failed planning attempts.

Nothing survived a failed enrichment session. A submission the model got wrong
lived only in the in-process message list; when the turn budget then ran out,
that list was garbage-collected and `_start_operation` REUSED the same
`planning_operations` row on retry — so the next attempt rebuilt its prompt from
scratch with zero memory and reproduced the same class of mistake until the
budget opened a block.

Its own table rather than a column on `planning_operations`, for three reasons:
that row is deliberately reused across a whole outage (its `created_at` is the
capacity ceiling's clock), one column would be overwritten every attempt and
destroy the sequence, and the attempt-timeline read would carry a multi-KB blob.
Its own table rather than the plan aggregate, because the aggregate rides the
version CAS and would roll back with the very transaction this must outlive.

Revision ID: 0014_planning_artifacts
Revises: 0013_provider_capacity
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_planning_artifacts"
down_revision = "0013_provider_capacity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planning_artifacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL for plan-wide purposes (cycle architecture); a goal id for enrichment.
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("operation_id", sa.String(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        # Everything the attempt was derived from. A replay whose fingerprint no
        # longer matches is discarded silently: after an edit, a replan, or a
        # capability-catalog change the prior attempt is not merely unhelpful,
        # it describes work the model is no longer being asked to do.
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("rejection_reasons_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("turns_used", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('rejected', 'abandoned', 'committed')",
            name="ck_planning_artifacts_outcome",
        ),
    )
    op.create_index(
        "ix_planning_artifacts_lookup",
        "planning_artifacts",
        ["plan_id", "goal_id", "purpose", "created_at"],
    )
    op.create_index(
        "uq_planning_artifacts_operation_sequence",
        "planning_artifacts",
        ["operation_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_planning_artifacts_operation_sequence", table_name="planning_artifacts")
    op.drop_index("ix_planning_artifacts_lookup", table_name="planning_artifacts")
    op.drop_table("planning_artifacts")
