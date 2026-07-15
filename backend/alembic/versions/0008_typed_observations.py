"""Typed operational observation metadata.

Evolves the existing agent_events stream in place. Existing rows are preserved
and explicitly marked as legacy evidence; new typed observations can retain
correlation, provenance, quality, schema version, and recorded time.

Revision ID: 0008_typed_observations
Revises: 0007_execution_ledger
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_typed_observations"
down_revision = "0007_execution_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_events", sa.Column("goal_id", sa.String(), nullable=True))
    op.add_column("agent_events", sa.Column("run_id", sa.String(), nullable=True))
    op.add_column("agent_events", sa.Column("attempt_id", sa.String(), nullable=True))
    op.add_column(
        "agent_events",
        sa.Column("observation_kind", sa.String(), nullable=True),
    )
    op.add_column(
        "agent_events",
        sa.Column("source", sa.String(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "agent_events",
        sa.Column(
            "quality",
            sa.String(),
            nullable=False,
            server_default="legacy_unknown",
        ),
    )
    op.add_column(
        "agent_events",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_events",
        sa.Column("source_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_events",
        sa.Column("recorded_at", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE agent_events SET observation_kind = type, source_sequence = seq, "
        "recorded_at = occurred_at"
    )
    op.create_index("ix_agent_events_run", "agent_events", ["run_id", "id"])
    op.create_index(
        "ix_agent_events_attempt_id",
        "agent_events",
        ["attempt_id", "id"],
    )
    op.create_index(
        "ix_agent_events_kind",
        "agent_events",
        ["observation_kind", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_kind", table_name="agent_events")
    op.drop_index("ix_agent_events_attempt_id", table_name="agent_events")
    op.drop_index("ix_agent_events_run", table_name="agent_events")
    op.drop_column("agent_events", "recorded_at")
    op.drop_column("agent_events", "source_sequence")
    op.drop_column("agent_events", "schema_version")
    op.drop_column("agent_events", "quality")
    op.drop_column("agent_events", "source")
    op.drop_column("agent_events", "observation_kind")
    op.drop_column("agent_events", "attempt_id")
    op.drop_column("agent_events", "run_id")
    op.drop_column("agent_events", "goal_id")
