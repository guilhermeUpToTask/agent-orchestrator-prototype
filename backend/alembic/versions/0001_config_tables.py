"""config tables: projects, providers, models, agents, secrets, active_projects

Revision ID: 0001_config_tables
Revises:
Create Date: 2026-06-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_config_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("repo_url", sa.String(), nullable=False),
        sa.Column("default_branch", sa.String(), nullable=False, server_default="main"),
        sa.Column("github_secret_uri", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("secret_uri", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("default_model", sa.String(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "registered_models",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "provider_id",
            sa.String(),
            sa.ForeignKey("model_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),
    )
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("runtime_type", sa.String(), nullable=False),
        sa.Column(
            "provider_id",
            sa.String(),
            sa.ForeignKey("model_providers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "secrets",
        sa.Column("uri", sa.String(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("wrapped_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "active_projects",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("active_projects")
    op.drop_table("secrets")
    op.drop_table("agent_definitions")
    op.drop_table("registered_models")
    op.drop_table("model_providers")
    op.drop_table("projects")
