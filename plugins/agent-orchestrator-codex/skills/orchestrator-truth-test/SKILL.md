---
name: orchestrator-truth-test
description: Select and run the minimum trustworthy Agent Orchestrator tests for a request or changed paths, preserving dual-backend fake/SQLite parity and escalating to the full quality gate. Use when planning verification, fixing regressions, reviewing a diff, diagnosing CI, or changing domain, adapters, worker, API, git workspace, reasoner, migrations, or frontend contracts.
---

# Orchestrator Truth Test

1. Run:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-truth-test/scripts/select_tests.py [paths...]
   ```

   Omit paths to inspect the working-tree diff.
2. Read [references/test-map.md](references/test-map.md) for the selected subsystem.
3. Run focused commands first and preserve complete failure output.
4. Add a regression test for every fixed defect.
5. Use `FakeClock.advance()` instead of sleeping, `tmp_path` for files, and `monkeypatch` for environment.
6. If an adapter contract changes, prove both in-memory and SQLite semantics.
7. Finish backend changes with `make check`; finish frontend/API changes with frontend build and contract drift verification.

Never run the cost-gated `llm` smoke test without explicit authorization and credentials.
