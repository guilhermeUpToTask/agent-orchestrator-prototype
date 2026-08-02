"""Stable execution run and attempt identity.

Adds an operational execution ledger outside the Plan aggregate. Run/attempt
rows share the Plan UnitOfWork transaction so an invocation identity exists
before runtime side effects and finalizes atomically with workflow state.

Revision ID: 0007_execution_ledger
Revises: 0006_pause_and_telemetry
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_execution_ledger"
down_revision = "0006_pause_and_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'retrying', 'succeeded', 'failed', 'abandoned')",
            name="ck_execution_runs_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_runs_plan_task",
        "execution_runs",
        ["plan_id", "goal_id", "task_id", "id"],
    )
    op.create_index(
        "uq_execution_runs_active_task",
        "execution_runs",
        ["plan_id", "goal_id", "task_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('running', 'retrying')"),
    )

    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("task_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'abandoned')",
            name="ck_execution_attempts_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "goal_id",
            "task_id",
            "number",
            name="uq_execution_attempts_task_number",
        ),
    )
    op.create_index(
        "ix_execution_attempts_open",
        "execution_attempts",
        ["status", "started_at"],
    )
    op.create_index("ix_execution_attempts_run", "execution_attempts", ["run_id", "number"])


def downgrade() -> None:
    op.drop_index("ix_execution_attempts_run", table_name="execution_attempts")
    op.drop_index("ix_execution_attempts_open", table_name="execution_attempts")
    op.drop_table("execution_attempts")
    op.drop_index("uq_execution_runs_active_task", table_name="execution_runs")
    op.drop_index("ix_execution_runs_plan_task", table_name="execution_runs")
    op.drop_table("execution_runs")
