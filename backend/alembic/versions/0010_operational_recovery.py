"""Operational planning, attempt evidence, and provider circuits.

Revision ID: 0010_operational_recovery
Revises: 0009_cyclic_project_plan
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_operational_recovery"
down_revision = "0009_cyclic_project_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("execution_attempts") as batch:
        batch.add_column(sa.Column("last_liveness_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("timeout_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("runtime", sa.String(), nullable=True))
        batch.add_column(sa.Column("provider_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("model_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("failure_kind", sa.String(), nullable=True))
        batch.add_column(sa.Column("provider_code", sa.String(), nullable=True))
        batch.add_column(sa.Column("retryable", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retry_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("limit_scope", sa.String(), nullable=True))
        batch.add_column(sa.Column("exit_code", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("safe_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stdout_tail", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stderr_tail", sa.Text(), nullable=True))

    op.execute(
        "UPDATE execution_attempts SET last_liveness_at = started_at "
        "WHERE last_liveness_at IS NULL"
    )

    op.create_table(
        "planning_operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("target_goal_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("last_liveness_at", sa.String(), nullable=True),
        sa.Column("model_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runtime", sa.String(), nullable=True),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("failure_kind", sa.String(), nullable=True),
        sa.Column("retry_at", sa.String(), nullable=True),
        sa.Column("safe_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'started', 'waiting_for_user', 'committed', "
            "'failed', 'backing_off')",
            name="ck_planning_operations_status",
        ),
    )
    op.create_index(
        "ix_planning_operations_plan",
        "planning_operations",
        ["plan_id", "created_at", "id"],
    )
    op.create_index(
        "ix_planning_operations_active",
        "planning_operations",
        ["plan_id", "purpose", "target_goal_id", "status"],
    )

    op.create_table(
        "runtime_circuits",
        sa.Column("runtime", sa.String(), primary_key=True),
        sa.Column("provider_id", sa.String(), primary_key=True),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.String(), nullable=False),
        sa.Column("retry_at", sa.String(), nullable=False),
        sa.Column("last_failure_kind", sa.String(), nullable=False),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.Column("manual_intervention", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_runtime_circuits_retry", "runtime_circuits", ["retry_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_circuits_retry", table_name="runtime_circuits")
    op.drop_table("runtime_circuits")
    op.drop_index("ix_planning_operations_active", table_name="planning_operations")
    op.drop_index("ix_planning_operations_plan", table_name="planning_operations")
    op.drop_table("planning_operations")
    with op.batch_alter_table("execution_attempts") as batch:
        for column in (
            "stderr_tail",
            "stdout_tail",
            "safe_message",
            "exit_code",
            "limit_scope",
            "retry_at",
            "retryable",
            "provider_code",
            "failure_kind",
            "model_id",
            "provider_id",
            "runtime",
            "timeout_seconds",
            "last_liveness_at",
        ):
            batch.drop_column(column)
