"""Per-provider / per-model capacity metadata (domain unfreeze #16).

An in-flight ceiling is a property of the PROVIDER, not of the orchestrator: a
paid tier, a free aggregator, and a local single-GPU server have wildly
different ones. `capacity_scope` records whether the provider's upstream limits
are per routed model (an aggregator) or shared across one endpoint (a
self-hosted deployment), so policy never branches on a provider name.

All columns nullable with no default: existing rows migrate untouched and keep
falling back to the global config keys.

Revision ID: 0013_provider_capacity
Revises: 0012_runtime_circuit_probe
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_provider_capacity"
down_revision = "0012_runtime_circuit_probe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("max_inflight", sa.Integer(), nullable=True))
    op.add_column("providers", sa.Column("capacity_scope", sa.String(), nullable=True))
    op.add_column("models", sa.Column("max_inflight", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "max_inflight")
    op.drop_column("providers", "capacity_scope")
    op.drop_column("providers", "max_inflight")
