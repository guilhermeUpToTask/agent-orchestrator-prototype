"""Reference data: capabilities, agents (+capability join), providers, models,
projects, two-tier config.

Revision ID: 0002_reference
Revises: 0001_core
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_reference"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tools", sa.Text(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("model_role", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("default_retry", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "agent_capabilities",
        sa.Column(
            "agent_id",
            sa.String(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "capability_id",
            sa.String(),
            sa.ForeignKey("capabilities.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("api_key_ref", sa.String(), nullable=False),
    )

    op.create_table(
        "models",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(),
            sa.ForeignKey("providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("repo_url", sa.String(), nullable=True),
    )

    op.create_table(
        "config",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("config")
    op.drop_table("projects")
    op.drop_table("models")
    op.drop_table("providers")
    op.drop_table("agent_capabilities")
    op.drop_table("agents")
    op.drop_table("capabilities")
