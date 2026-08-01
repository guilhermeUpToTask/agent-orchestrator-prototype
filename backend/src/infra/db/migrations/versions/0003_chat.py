"""Plan chat history (DISCOVERY / REPLANNING conversations).

Revision ID: 0003_chat
Revises: 0002_reference
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_chat"
down_revision = "0002_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id", sa.String(), sa.ForeignKey("plans.id"), nullable=False
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_plan_chat_messages_plan", "plan_chat_messages", ["plan_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_plan_chat_messages_plan", table_name="plan_chat_messages")
    op.drop_table("plan_chat_messages")
