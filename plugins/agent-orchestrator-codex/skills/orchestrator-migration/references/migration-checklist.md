# Migration checklist

- Confirm one Alembic head and a continuous `down_revision` chain.
- Preserve data through renames and type changes.
- Backfill before introducing non-null constraints.
- Keep SQLite limitations in mind; use batch operations when required.
- Update `backend/src/infra/db/tables.py`.
- Update repository encode/decode and reference guards.
- Keep fake semantics aligned when the persistence contract changes.
- Update Pydantic/API/frontend contracts when persisted data is public.
- Test a fresh upgrade and an upgrade from the immediate predecessor.
- Run `test_migrations.py`, affected repository tests, and the truth suite.
