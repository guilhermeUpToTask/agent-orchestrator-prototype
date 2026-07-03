"""Core schema: plans (JSON document + lease), plan_requests, outbox,
agent_events, secrets.

Fresh migration chain (clean break — the pre-refactor schema and its data were
discarded; see docs/DESIGN_NOTES.md and the integration plan).

Revision ID: 0001_core
Revises:
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("claimed_at", sa.Integer(), nullable=True),
        sa.Column("lease_expires_at", sa.Integer(), nullable=True),
        sa.Column("lease_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_plans_claim", "plans", ["phase", "lease_expires_at"])

    op.create_table(
        "plan_requests",
        sa.Column("request_id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id", sa.String(), sa.ForeignKey("plans.id"), nullable=False
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False, unique=True),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.Column("delivered_at", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_outbox_undelivered",
        "outbox",
        ["id"],
        sqlite_where=sa.text("delivered_at IS NULL"),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False, unique=True),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
    )

    op.create_table(
        "secrets",
        sa.Column("uri", sa.String(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("wrapped_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("secrets")
    op.drop_table("agent_events")
    op.drop_index("ix_outbox_undelivered", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("plan_requests")
    op.drop_index("ix_plans_claim", table_name="plans")
    op.drop_table("plans")
