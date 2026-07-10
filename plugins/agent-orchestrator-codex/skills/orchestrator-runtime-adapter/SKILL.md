---
name: orchestrator-runtime-adapter
description: Add or modify Agent Orchestrator reasoners, CLI agent runtimes, providers, model bindings, secret resolution, failure taxonomy, dependency probes, runtime status, factories, telemetry, and tests. Use for new coding-agent integrations, OpenAI-compatible reasoners, runner command changes, provider catalog behavior, or runtime configuration failures.
---

# Orchestrator Runtime Adapter

1. Decide whether the extension implements the two-method Reasoner port or the AgentRunner port.
2. Query graphify for the port-to-factory-to-container path.
3. Run the surface report:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-runtime-adapter/scripts/runtime_surface.py
   ```

4. Read [references/extension-points.md](references/extension-points.md).
5. Keep environment reads in `AppContainer`; resolve runtime choice from SQLite config and catalog bindings.
6. Keep stub/dry-run paths independent of secrets.
7. Map failures through the shared `FailureKind`; do not invent adapter-only retry rules.
8. Update dependency probes and runtime status with the factory.
9. Use scripted fake CLIs or `FakeLLMClient`; never call paid providers in normal tests.
10. Run runtime units, factory/taxonomy integration tests, relevant full-cycle tests, and `make check`.
