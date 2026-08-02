"""acceptance_runs — the advisory verdict of one cycle acceptance run.

Fills the `cycle_verification` slot that `planner_orchestrator.py:932` has named
since ADR-003 with no behaviour behind it. A row records what a
`ProjectEnvironment` concluded when the assembled tree was booted and a scenario
run against it, at one of two trigger points: each goal merge (early signal) and
before the publication gate.

Deliberately NOT on the Plan aggregate. The verdict is advisory and never gates
anything, so it is an operational ledger like `goal_promotions` and
`planning_artifacts` rather than domain state — which also means no un-freeze.

`ON DELETE CASCADE` on plan_id, as migration 0015 requires of every plan-scoped
table; `test_delete_plan_leaves_nothing.py` fails if a new one forgets.

Revision ID: 0018_acceptance_runs
Revises: 0017_goal_promotions
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_acceptance_runs"
down_revision = "0017_goal_promotions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acceptance_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cycle_id", sa.String(), nullable=False),
        # Null for a pre-publication run: it observes the whole cycle, not one goal.
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("trigger", sa.String(), nullable=False),  # goal_merge | pre_publication
        sa.Column("ref", sa.String(), nullable=False),  # the git ref that was booted
        # passed | failed | errored | skipped. `skipped` is first class: no
        # environment configured must read differently from "we tried and could not".
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_acceptance_runs_cycle",
        "acceptance_runs",
        ["plan_id", "cycle_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_acceptance_runs_cycle", table_name="acceptance_runs")
    op.drop_table("acceptance_runs")
