"""Human pause gate + plan-scoped agent events (un-freeze #3).

- plans.paused: the durable pause claim gate — a human pause command or the
  auto-pause on a terminal task failure arms it; the claim predicate skips
  paused plans until resume clears it. Projected from Plan.paused.
- agent_events.task_id becomes nullable: plan-scoped telemetry rows (the
  reasoner's llm.call usage events) have no task.
- ix_agent_events_plan_task: the per-plan / per-task history read path.

Revision ID: 0006_pause_and_telemetry
Revises: 0005_plan_backoff
Create Date: 2026-07-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_pause_and_telemetry"
down_revision = "0005_plan_backoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("paused", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("agent_events") as batch:
        batch.alter_column("task_id", existing_type=sa.String(), nullable=True)
    op.create_index(
        "ix_agent_events_plan_task", "agent_events", ["plan_id", "task_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_plan_task", table_name="agent_events")
    with op.batch_alter_table("agent_events") as batch:
        batch.alter_column("task_id", existing_type=sa.String(), nullable=False)
    op.drop_column("plans", "paused")
