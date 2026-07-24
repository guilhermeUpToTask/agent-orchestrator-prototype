"""Per-goal worker leases for goal-level parallelism.

Revision ID: 0011_goal_leases
Revises: 0010_operational_recovery
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_goal_leases"
down_revision = "0010_operational_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_leases",
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("claimed_at", sa.Integer(), nullable=True),
        sa.Column("lease_expires_at", sa.Integer(), nullable=True),
        sa.Column("lease_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plan_id", "goal_id"),
    )
    op.create_index(
        "ix_goal_leases_claim",
        "goal_leases",
        ["claimed_by", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_goal_leases_claim", table_name="goal_leases")
    op.drop_table("goal_leases")
