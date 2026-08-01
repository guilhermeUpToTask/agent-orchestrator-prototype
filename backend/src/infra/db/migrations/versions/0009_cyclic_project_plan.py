"""Cyclic ProjectPlan ownership and claim status.

Revision ID: 0009_cyclic_project_plan
Revises: 0008_typed_observations
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_cyclic_project_plan"
down_revision = "0008_typed_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch:
        batch.add_column(
            sa.Column(
                "project_id",
                sa.String(),
                sa.ForeignKey("projects.id", name="fk_plans_project_id_projects"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("status", sa.String(), nullable=False, server_default="blocked"))
        batch.add_column(
            sa.Column("pause_requested", sa.Integer(), nullable=False, server_default="0")
        )

    mapped_status = (
        "CASE phase "
        "WHEN 'done' THEN 'idle' "
        "WHEN 'failed' THEN 'blocked' "
        "WHEN 'running' THEN 'running' "
        "WHEN 'review' THEN 'waiting' "
        "WHEN 'awaiting_review' THEN 'waiting' "
        "WHEN 'enriching' THEN 'running' "
        "WHEN 'architecture' THEN 'running' "
        "WHEN 'discovery' THEN 'waiting' "
        "WHEN 'replanning' THEN 'waiting' "
        "ELSE 'blocked' END"
    )
    # Released rows have no authoritative project relation. Preserve the honest
    # phase mapping in JSON, then quarantine them as readable/non-claimable.
    op.execute(f"UPDATE plans SET status = {mapped_status}")
    op.execute(
        f"""
        UPDATE plans
        SET data = json_set(
            data,
            '$.legacy_phase', phase,
            '$.legacy_mapped_status', {mapped_status},
            '$.project_id', NULL,
            '$.status', 'blocked',
            '$.pause_requested', json('false'),
            '$.block', json_object(
                'id', 'legacy-project-binding:' || id,
                'kind', 'project_binding',
                'explanation', 'Legacy plan has no authoritative project binding',
                'stage', 'project_binding',
                'evidence_refs', json_array(),
                'legal_resolutions', json_array('bind_project'),
                'created_at', updated_at
            )
        ),
        status = 'blocked'
        """
    )

    op.drop_index("ix_plans_claim", table_name="plans")
    op.create_index(
        "ix_plans_claim",
        "plans",
        ["status", "pause_requested", "lease_expires_at"],
    )
    op.create_index(
        "uq_plans_project_id",
        "plans",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("project_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_plans_project_id", table_name="plans")
    op.drop_index("ix_plans_claim", table_name="plans")
    op.create_index("ix_plans_claim", "plans", ["phase", "lease_expires_at"])
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("pause_requested")
        batch.drop_column("status")
        batch.drop_column("project_id")
