---
name: orchestrator-doc-audit
description: Audit and update Agent Orchestrator documentation so current architecture, CLI commands, configuration, lifecycle behavior, known issues, decisions, roadmap items, tests, and generated API facts agree with code. Use after behavior changes, when reviewing a PR, when fixing known issues, or when documentation may be stale or contradictory.
---

# Orchestrator Documentation Audit

1. Query graphify for the changed behavior and documentation nodes.
2. Run:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-doc-audit/scripts/audit_docs.py
   ```

3. Read [references/doc-policy.md](references/doc-policy.md).
4. Verify current claims against source and exact configuration files.
5. Put implemented behavior in `docs/architecture/`; put unimplemented work in `ROADMAP.md`.
6. Remove a fixed item from `known-issues.md` and require its regression test.
7. Record every deliberate domain unfreeze in the decision log.
8. Keep history immutable except for broken links or explicit archival metadata.
9. Re-query graphify after edits and run link/config checks plus relevant code gates.

Report contradictions with both source locations; do not silently choose one.
