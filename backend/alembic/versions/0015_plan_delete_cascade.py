"""Make every plan-scoped table cascade, so deleting a plan can leave nothing.

There was no way to dispose of a plan at all: the cyclic root is never terminal,
a project owns exactly one long-lived plan (ADR-003), and re-posting a brief
reopens the SAME plan with another cycle. Adding `DELETE /api/plans/{id}` exposed
that the schema could not support it cleanly. Four tables hold rows for a plan
that a `DELETE FROM plans` would not collect:

  plan_requests       FK to plans.id with NO ON DELETE -> the delete is REJECTED
  plan_chat_messages  FK to plans.id with NO ON DELETE -> the delete is REJECTED
  outbox              plan_id column, no FK at all     -> silently ORPHANED
  agent_events        plan_id column, no FK at all     -> silently ORPHANED

The alternative was an explicit sweep list inside the repository adapter, which
works until someone adds the fifth table and no test notices. Pushing the rule
into the schema makes "no leftovers" a database guarantee, and a new plan-scoped
table now has to opt OUT deliberately rather than be remembered.

Adding a foreign key to `agent_events` is safe despite that writer being
best-effort and off-transaction: `SqliteAgentEventSink.emit` already swallows and
logs every exception, so an event racing a plan deletion becomes logged telemetry
loss — the correct outcome for a plan that no longer exists.

SQLite cannot alter a constraint in place, so each table is rebuilt with
`batch_alter_table`. Pre-existing orphans (rows whose plan is already gone,
which the missing foreign keys made possible) are deleted first: the rebuild
enforces the new constraint and would otherwise fail on legacy data.

Revision ID: 0015_plan_delete_cascade
Revises: 0014_planning_artifacts
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_plan_delete_cascade"
down_revision = "0014_planning_artifacts"
branch_labels = None
depends_on = None

# Matches the names below, so a reflected anonymous FK resolves to the same name
# `drop_constraint` is given.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}

# table -> the foreign-key constraint name used from here on.
_TABLES = {
    "plan_requests": "fk_plan_requests_plan_id_plans",
    "plan_chat_messages": "fk_plan_chat_messages_plan_id_plans",
    "outbox": "fk_outbox_plan_id_plans",
    "agent_events": "fk_agent_events_plan_id_plans",
}


def upgrade() -> None:
    connection = op.get_bind()

    for table in _TABLES:
        # Orphans are only possible because the constraint was missing; the
        # rebuild below enforces it, so they have to go first or it fails.
        connection.execute(
            sa.text(
                f"DELETE FROM {table} "
                "WHERE plan_id IS NOT NULL "
                "AND plan_id NOT IN (SELECT id FROM plans)"
            )
        )

    # outbox / agent_events never had a foreign key: just add one.
    for table in ("outbox", "agent_events"):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.create_foreign_key(
                _TABLES[table], "plans", ["plan_id"], ["id"], ondelete="CASCADE"
            )

    # plan_requests / plan_chat_messages already have an UNNAMED foreign key with
    # no ON DELETE. Adding a second one leaves both in place, which is how the
    # first cut of this migration produced `on_delete=['NO ACTION', 'CASCADE']`.
    # The naming convention gives the reflected anonymous constraint a
    # deterministic name so it can be dropped — the documented SQLite pattern.
    for table in ("plan_requests", "plan_chat_messages"):
        with op.batch_alter_table(
            table, naming_convention=_NAMING_CONVENTION, recreate="always"
        ) as batch:
            batch.drop_constraint(_TABLES[table], type_="foreignkey")
            batch.create_foreign_key(
                _TABLES[table], "plans", ["plan_id"], ["id"], ondelete="CASCADE"
            )


def downgrade() -> None:
    # Restores the pre-0015 shape: the two FKs without ON DELETE, and none on the
    # telemetry tables. Rows are not recoverable, only the constraints.
    for table in ("outbox", "agent_events"):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(_TABLES[table], type_="foreignkey")

    for table in ("plan_requests", "plan_chat_messages"):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(_TABLES[table], type_="foreignkey")
            batch.create_foreign_key(_TABLES[table], "plans", ["plan_id"], ["id"])
