"""Runtime-circuit limit scope + single-flight half-open probe.

`limit_scope` records which capacity tier the provider reported (concurrency vs
quota vs daily quota) so policy can respond differently to each. `probe_holder`
/ `probe_started_at` make the half-open probe single-flight: without them every
concurrent goal worker past `retry_at` probes simultaneously.

Revision ID: 0012_runtime_circuit_probe
Revises: 0011_goal_leases
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_runtime_circuit_probe"
down_revision = "0011_goal_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_circuits", sa.Column("limit_scope", sa.String(), nullable=True))
    op.add_column("runtime_circuits", sa.Column("probe_holder", sa.String(), nullable=True))
    op.add_column("runtime_circuits", sa.Column("probe_started_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("runtime_circuits", "probe_started_at")
    op.drop_column("runtime_circuits", "probe_holder")
    op.drop_column("runtime_circuits", "limit_scope")
