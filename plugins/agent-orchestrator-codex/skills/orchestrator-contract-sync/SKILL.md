---
name: orchestrator-contract-sync
description: Synchronize Agent Orchestrator FastAPI routes and DTOs with the exported OpenAPI schema, generated frontend TypeScript, handwritten aggregate read models, queries, event listeners, and UI consumers. Use for API, schema, response, event, frontend data-model, or generated-type changes and when CI reports API drift.
---

# Orchestrator Contract Sync

1. Trace the backend symbol to frontend consumers with graphify.
2. Change backend DTOs and routers first; keep HTTP error mapping centralized.
3. Run:

   ```bash
   python plugins/agent-orchestrator-codex/skills/orchestrator-contract-sync/scripts/verify_contracts.py
   ```

4. Inspect generated diffs; never hand-edit `frontend/src/types/generated/`.
5. Compare changed schemas with `frontend/src/types/ui.ts`, which hand-declares the aggregate detail model.
6. Update `frontend/src/lib/api.ts`, queries, stores, and event listeners as required.
7. Add or update API integration tests.
8. Run `npm run build`, the drift check, and `make check`.

Read [references/contract-flow.md](references/contract-flow.md) for SSE and handwritten-model rules.
