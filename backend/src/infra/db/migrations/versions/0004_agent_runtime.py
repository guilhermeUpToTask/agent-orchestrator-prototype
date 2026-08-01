"""Per-agent runtime resolution: agents carry runtime_type + provider/model refs.

Revision ID: 0004_agent_runtime
Revises: 0003_chat
Create Date: 2026-07-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_agent_runtime"
down_revision = "0003_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "runtime_type", sa.String(), nullable=False, server_default="pi"
        ),
    )
    op.add_column("agents", sa.Column("provider_id", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("model_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "model_id")
    op.drop_column("agents", "provider_id")
    op.drop_column("agents", "runtime_type")
