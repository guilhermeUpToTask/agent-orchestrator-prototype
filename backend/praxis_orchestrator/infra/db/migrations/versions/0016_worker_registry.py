"""Record that a worker is alive, in its own un-scoped table.

Before this migration, liveness existed only as `claimed_by` / `lease_expires_at`
on `plans` and `goal_leases` — both of which prove a worker is BUSY, not that it
is RUNNING. An idle worker (nothing claimed yet) holds neither, so "worker
running, nothing to do" and "worker never started" were indistinguishable
through the API — exactly the case the J1/J2 setup checklist hits most often,
before the first plan is ever claimed.

`workers` deliberately carries no foreign key to `plans` and needs no
`ON DELETE CASCADE`: a worker process is not plan-scoped, it outlives every
plan a project ever runs, and it must go on reporting liveness across a plan
being deleted, replanned, or never created at all. This is the one exception to
migration 0015's rule that a new plan-scoped table opts into cascade by
default — `workers` opts OUT because it was never plan-scoped to begin with.
`test_delete_plan_leaves_nothing.py` is unaffected for the same reason.

Rows are UPSERTED by `worker_id`, never appended: the heartbeat that keeps this
table current runs every 5 seconds for the life of the process (see
`WorkerRegistry.beat` / `praxis_orchestrator/infra/db/worker_registry.py`), so an append-only
table would grow without bound. A restarted worker reusing the same
`worker_id` overwrites its own row in place, so the table's size tracks the
size of the fleet, not its uptime. `worker_id` is therefore the primary key,
not an autoincrement surrogate — there is exactly one row of truth per worker
identity.

No backfill: an empty table means "no worker has ever reported", which is
exactly true immediately after this migration runs and before the next worker
boots.

Revision ID: 0016_worker_registry
Revises: 0015_plan_delete_cascade
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_worker_registry"
down_revision = "0015_plan_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.String(), primary_key=True),
        # agent_runner.mode at boot ("dry-run" | "real"): what this worker is
        # actually executing tasks with, not just the config default.
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=False),
        sa.Column("poll_seconds", sa.Float(), nullable=False),
        sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_goals", sa.Integer(), nullable=False),
        sa.Column("inflight_goals", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("workers")
