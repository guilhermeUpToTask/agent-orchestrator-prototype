---
name: orchestrator-domain-guard
description: Implement or review Agent Orchestrator domain and application changes while protecting the frozen phase machine, aggregate authority, dependency boundaries, injected time, CAS, transactional outbox, lease semantics, retry and pause gates, and fake/SQLite parity. Use for changes under backend/src/domain or backend/src/app and for worker lifecycle behavior.
---

# Orchestrator Domain Guard

1. Query graphify for the changed aggregate, transition, handler, and tests.
2. Run the static boundary check:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-domain-guard/scripts/check_boundaries.py
   ```

3. Read [references/invariants.md](references/invariants.md).
4. Before editing the frozen domain, establish a deliberate unfreeze and update `docs/decisions/decision-log.md`.
5. Make transitions through `Plan`; never mutate goal/task state in a use case.
6. Keep side effects outside transactions. In finalize transactions, re-read and re-guard.
7. Preserve `plan.bump_version()` followed by repository save and same-transaction outbox writes.
8. Update in-memory and SQLite semantics together.
9. Add a regression test using `FakeClock`, `tmp_path`, or the dual-backend `env_factory`; do not use sleeps.
10. Run the focused truth tests, then `make check`.

When reviewing, report violations before style concerns.
