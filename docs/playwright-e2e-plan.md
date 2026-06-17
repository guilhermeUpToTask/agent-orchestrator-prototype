# Playwright E2E Plan — Architecture Phase Workflow (deferred)

> Status: **not yet implemented.** The architecture-phase bug fix (this branch)
> is complete and verified via the backend test suite + a live dry-run HTTP
> walkthrough. This document captures everything needed to add the browser E2E
> in a focused follow-up session.

## Goal

A Playwright spec that drives the real frontend against a live dry-run backend
and proves the workflow the operator previously got stuck on:

```
discovery → approve brief → architecture auto-drafts → status "completed"
          → approve architecture → first-phase goal dispatched → goal populated
            with JIT tasks (test-writer + implementer)
```

This is the regression guard for the "can't move past architecture / approve
dangles on 409" bug.

## What already works (verified this session, no UI)

Driving the dry-run API by hand confirmed the whole backend chain:

- `POST /api/plan/discovery/start` → stub interactive runtime drafts a brief.
- `POST /api/plan/approve-brief` → returns `plan_status: "architecture"` **and
  auto-launches** the architecture session (no separate Draft click).
- `GET /api/plan/architecture/status` → `running` then `completed` with
  `decisions` + `phases` (the reload-safe readiness the UI gates on).
- `POST /api/plan/approve-architecture` with `{"decision_ids": []}` → applies
  **all** decisions and dispatches the first phase goal → `phase_active`.
- `goal.unblocked` → embedded `TaskGraphOrchestrator` → `PlanGoalTasksUseCase`
  → `StubPlannerRuntime` now emits 2 TDD tasks → goal populated.

## Environment constraints (learned this session — design around these)

1. **Dry-run is single-process / no Redis.** `AGENT_MODE=dry-run system api`
   uses the in-memory event adapter and runs the task-manager, goal-orchestrator
   and reconciler as in-process threads. The Redis→SSE bridge is **skipped** in
   dry-run (`src/api/server.py` guards it on `mode != "dry-run"`).
   - Consequence: worker/coordinator domain events (e.g. `task.created`,
     `task.status_changed`) do **not** reach the browser over SSE in dry-run.
     The UI updates goals via React Query refetch (window focus / `staleTime` /
     explicit invalidation), not live SSE, for those events. The spec should
     **poll the UI / refetch** rather than wait on live task SSE.
   - Router-originated `publish_sse(...)` calls (plan status, decision/phase
     proposed, architecture progress) DO reach the browser even in dry-run.

2. **Task execution needs separate worker processes + Redis.** `system start`
   spawns one `system worker` subprocess per active agent; those workers share
   state via Redis. In dry-run (in-memory, cross-process invisible) they can't
   receive `task.assigned`. So **actual task execution is out of scope for the
   dry-run browser E2E** — assert goals get *populated* with tasks, not that
   tasks reach `succeeded`. Task-level execution is already covered by
   `tests/integration/test_e2e_full.py` (real fakeredis + SimulatedAgentRuntime).

3. **Boot in the agent sandbox gets SIGTERM'd.** Long-running servers launched
   from the tool harness were killed (exit 144). Run the E2E locally / in CI,
   not from inside the agent sandbox. Playwright's `webServer` config handles
   boot+teardown in those environments.

4. **Global config lives at `~/.orchestrator/config.json`.** `orchestrate init
   --defaults` writes it (project name "restapi server"). The E2E global-setup
   must ensure a dry-run config + project spec exist before booting.

## Implementation steps

1. **Deps:** `cd frontend && npm i -D @playwright/test && npx playwright install
   chromium`.

2. **`frontend/playwright.config.ts`:**
   - `webServer: [ <Vite dev>, <API> ]` (Playwright starts both, waits on ports).
     - API command: `AGENT_MODE=dry-run python -m src.infra.cli.main system api
       --port 8000` with `cwd` at repo root. Add a global-setup that runs
       `orchestrate init --defaults` (idempotent) first if no config exists.
     - Vite: `npm run dev`, with `VITE_API_URL=http://127.0.0.1:8000`.
   - `use.baseURL` = Vite URL; single chromium project; `reuseExistingServer`
     in dev.

3. **`frontend/e2e/architecture-flow.spec.ts`:** drive the UI:
   - Start discovery (rail "Start discovery"), wait for brief-ready gate.
   - Open gate → "Approve brief" (two-step confirm). Assert rail shows
     "Drafting architecture…" (auto-start; `activeRun` optimistic + status poll).
   - Wait for the architecture gate to become available — gate appears only on
     `completedRuns.includes('architecture')` (driven by
     `GET /architecture/status` → `completed`). Assert it does **not** appear
     while `state === 'running'` (the core regression — no premature approve).
   - Open gate → "Approve architecture". Assert no 409 toast; plan advances to
     `phase_active`; a phase appears in the rail.
   - Navigate to Goals; poll/refetch until the dispatched goal shows 2 tasks
     (JIT populated). Use `expect.poll` hitting the UI or trigger a refetch.

4. **`package.json`:** add `"test:e2e": "playwright test"`.

5. **CI:** a job that installs Python deps + browsers and runs `npm run test:e2e`
   (Playwright boots both servers). Keep it separate from unit/integration.

## Assertions checklist

- Architecture approval gate is **absent** while the run is `running`.
- Gate appears once status is `completed`; approving does **not** 409.
- After approval: `plan.status === 'phase_active'`, ≥1 phase rendered.
- Dispatched goal eventually shows 2 JIT tasks (`write-tests`, `implement`).
- A page reload mid/after run keeps the correct gate (status-sync hydration).
