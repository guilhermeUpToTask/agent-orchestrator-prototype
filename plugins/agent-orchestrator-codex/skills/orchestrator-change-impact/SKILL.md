---
name: orchestrator-change-impact
description: Map an Agent Orchestrator request or diff to affected architectural layers, invariants, generated artifacts, documentation, and focused tests. Use before implementing or reviewing cross-layer changes, unfamiliar bugs, API changes, migrations, runtime adapters, worker behavior, or any change whose blast radius is uncertain.
---

# Orchestrator Change Impact

Build a change manifest before editing.

1. Run `graphify query "<request and likely subsystem>"`.
2. Use `graphify explain "<symbol>"` for central symbols and `graphify path "<A>" "<B>"` for cross-layer flow.
3. Run:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-change-impact/scripts/change_impact.py [paths...]
   ```

   Omit paths to classify the current working-tree diff.
4. Read only the reference sections named by the tool output.
5. Return or maintain a manifest with:
   - affected layers and source anchors
   - invariants at risk
   - generated artifacts
   - required documentation
   - focused tests
   - final gates
6. Re-run the tool after implementation to catch newly affected surfaces.

Do not treat graph proximity as proof of behavior. Verify exact configuration, schema, and workflow files verbatim before changing them.

Read [references/change-matrix.md](references/change-matrix.md) when the output identifies more than one layer or when the request changes public behavior.
