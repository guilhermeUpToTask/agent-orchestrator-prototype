"""Plan-level durable backoff gate for worker-driven planning phases.

A transient reasoner failure (rate limit / upstream error) in ARCHITECTURE or
ENRICHING arms `plans.retry_not_before` (epoch seconds UTC); the claim predicate
skips the plan until it passes, so the worker backs off instead of hot-looping
the provider. Projected from Plan.planning_retry_not_before.

Revision ID: 0005_plan_backoff
Revises: 0004_agent_runtime
Create Date: 2026-07-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_plan_backoff"
down_revision = "0004_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans", sa.Column("retry_not_before", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("plans", "retry_not_before")
