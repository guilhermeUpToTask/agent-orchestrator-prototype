"""Record where a promoted goal's work actually landed.

The goal->cycle merge SHA was already persisted before this migration, but only
as an untyped string: `ExecutionHandler._promote_goal` appended `git:<sha>` to
`Cycle.evidence_refs`, a `list[str]` with no goal attribution, no branch name
and no timestamp. It answers "something was merged" and cannot answer "which
goal landed where" — which is the half of operator job J7 about where the code
went.

Branch names are stored as the adapter ACTUALLY built them rather than being
reconstructed at read time. Reconstruction is not safe here: the cyclic ladder
creates no `plan/<plan_id>` rung at all and keys task branches on run id, so a
read model deriving refs from the previously documented convention would have
advertised branches that are never created.

Plan-scoped, so it opts into migration 0015's cascade rule: deleting a plan must
leave nothing behind. `test_delete_plan_leaves_nothing.py` enforces this both by
name and by a schema-drift guard that fails any table carrying `plan_id`
without ON DELETE CASCADE.

No backfill. Existing `Cycle.evidence_refs` entries cannot be attributed to
goals except by promotion order, which would be a guess; cycles promoted before
this migration are served through the read model's
`unattributed_evidence_refs` field instead.

Revision ID: 0017_goal_promotions
Revises: 0016_worker_registry
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_goal_promotions"
down_revision = "0016_worker_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_promotions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cycle_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        # The branches the workspace adapter merged, stored verbatim.
        sa.Column("from_ref", sa.String(), nullable=False),
        sa.Column("into_ref", sa.String(), nullable=False),
        sa.Column("merge_sha", sa.String(), nullable=False),
        sa.Column("promoted_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_goal_promotions_cycle",
        "goal_promotions",
        ["plan_id", "cycle_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_goal_promotions_cycle", table_name="goal_promotions")
    op.drop_table("goal_promotions")
