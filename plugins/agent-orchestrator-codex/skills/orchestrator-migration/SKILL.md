---
name: orchestrator-migration
description: Evolve Agent Orchestrator SQLite schemas safely across SQLAlchemy tables, Alembic revisions, repositories, unit-of-work behavior, in-memory fakes, API contracts, and migration tests. Use when adding, removing, renaming, or changing persisted fields, indexes, constraints, reference data, secrets, leases, outbox, chat, or telemetry storage.
---

# Orchestrator Migration

1. Query graphify for the table, repository, entity/DTO, migration tests, and consumers.
2. Read [references/migration-checklist.md](references/migration-checklist.md).
3. Inspect the current Alembic head and predecessor.
4. Change table metadata and add one forward revision; do not rewrite released revisions.
5. Define defaults/backfills for existing rows before making fields non-null.
6. Update serializers, repositories, UoW boundaries, fake semantics, API contracts, and documentation.
7. Run:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-migration/scripts/check_migration_chain.py
   ```

8. Add tests for empty-database upgrade and upgrade from the predecessor.
9. Run affected repository/truth tests, contract sync when public data changes, then `make check`.

Do not access a developer database or `~/.orchestrator`; use temporary databases.
