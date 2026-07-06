   Fix the Architecture Phase (backend-first), then resilience, then E2E

ntext

ter approving the discovery brief, the operator is stuck in the architecture phase:

 - Pressing "Draft architecture" produces nothing â no live logs, no backend logs, the run silently "stops."
   - The UI nonetheless offers "Approve architecture" immediately, and approving it returns 409 forever.
   - The project never reaches the point where the JIT planner populates goals with tasks and starts executing them one by one.

 The user's intent for this phase: architecture turns the approved brief into a roadmap (decisions + a set of goals); once the operator approves it, the system should automatically run the Tactical JIT planner to fill each goal with TDD tasks and start executing tasks one at a time.

   This plan fixes the backend execution path first (make the run actually work + observable + resilient), verifies the post-approval JITâexecution chain, fixes the frontend gate desync and auto-start, and only then adds an end-to-end test.                            â

   Root-cause diagnosis (verified in code)

   1. The architecture run is invisible and fragile in the API path.
     - src/api/routers/plan.py::run_architecture spawns a daemon thread that calls orchestrator.run_architecture(...) but never wires a turn callback / live logger. Contrast the CLI path src/infra/cli/plan/commands.py::_bind_planner_hooks which calls orchestrator.set_turn_callback(...). Only the SSE event hook (decision/phase proposed) is wired in src/api/server.py::_wire_planner_sse_hook. â Zero live logs during the run ("nothing happens").
     - src/infra/runtime/planners/adapters/anthropic_adapter.py::send_turn is a synchronous, non-streaming messages.create(...) with no timeout; the client is built with no timeout (anthropic.Anthropic(api_key=...)). A single turn blocked ~151 s in the user's logs.   âcancel_check in base_agent_runtime.py is only checked between turns (lineÂ 73), never during the blocking call.
     - RunArchitectureUseCase calls the runtime with require_submit=False. If the model replies with text but no tool calls, base_agent_runtime.run_session breaks after one turn (lines 85â86) with submitted=False; assemble_roadmap then finds no decisions â session.fail("...ended without a usable roadmap") â only a plan.architecture_failed toast (easily missed). â "just stopped."
   2. Default model is incompatible with the always-on thinking param (fresh installs).                                                                                                                                                                                     â
     - Default planner_model = "claude-3-5-sonnet-20241022" (src/infra/settings/defaults.py:40, models.py:75); runtime fallback is the outdated claude-opus-4-6. The adapter always sends thinking={"type":"enabled", ...}. Claude 3.5 Sonnet does not support extended thinking â the API rejects every planner turn on a default-config project. (The user's own run used a thinking-capable model, hence 151 s, but new projects are dead on arrival.)                                                                                        â
   3. Telemetry blindness. src/app/telemetry/runtime_wrappers.py reads getattr(self._wrapped, "_model", "unknown"), but _model lives on ...runtime._adapter._model, not on the wrapped runtime â every llm.request/response logs model: 'unknown', token_usage: {}.
   4. Frontend gate desync + no auto-start (secondary, but it's what the operator sees).                                                                                                                                                                                    â
     - frontend/src/views/Overview.tsx:60-64 sets planGate = "Architecture approvalâ¦" purely on plan.status === 'architecture'; GatePanel.ArchitectureGate renders/enables Approve on the same condition. LifecycleRail is the only correct surface (gates on decisions.length > 0 || completedRuns.includes('architecture')). Approving before a completed session â ApproveArchitectureUseCase raises ValueError("No completed ARCHITECTURE session found") â 409 (generic ValueError handler in src/api/exceptions.py:95).
     - approve-brief's copy promises "starts architecture drafting," but nothing auto-starts the run.                                                                                                                                                                       â
   5. Post-approval JITâexecute chain is actually wired (good): embedded coordinators run in the API process by default (server.py::_coordinators_enabled, _start_coordinators), TaskGraphOrchestrator watches goal.unblocked â _on_goal_unblocked_jit â PlanGoalTasksUseCase.execute(goal_id) (src/app/orchestrator.py:214,373-403) â tasks â task-manager â worker. It is only ever blocked by never reaching a completed+approved architecture. Needs hardening + a regression test (and it reuses the same slow planner runtime, so it benefits from the same observability/timeout fixes).

   Plan                                                                                                                                                                                                                                                                     â

   â Order matters: (A) backend correctness/observability/resilience â (B) verify endpoints + JITâexecute â (C) frontend gate/auto-start â (D) E2E last.                                                                                                                    â
   â Model-ID / thinking changes touch Anthropic specifics â consult the claude-api skill to confirm current model IDs and extended-thinking support before editing.
                                                                                                                                                                                                                                                                            â
   A. Backend: make the architecture run work, be observable, and be resilient
                                                                                                                                                                                                                                                                            â
   A1 â Wire live logs + SSE progress for the API planner runs.
   - In src/api/routers/plan.py, before launching the architecture (and phase-review) thread, bind a turn callback that (a) emits structlog events (architecture.turn, reasoning/tool summaries) and (b) publishes an SSE progress event the rail already renders. Reuse the plan.jit_progress channel the frontend handles (queries.ts plan.jit_progress, rail summarizeProgress) or add plan.architecture_progress and handle it identically.                                                                                                       â
   - Reuse the CLI's logger plumbing: src/infra/logging/planner_logger.py::PlannerLiveLogger, planner_callback.py::StreamingPlannerCallback, live_logger.py::LiveLogger. Factor a small helper (e.g. _bind_api_planner_hooks(orchestrator, session)) so run_architecture, run_phase_review, and discovery share it. Set the callback per-run and clear it in the finally block.
   - Acceptance: starting a draft streams visible turn/progress lines to both backend logs and the rail's live session card.

   A2 â Timeouts + responsive cancellation + actionable failures in the planner runtime.                                                                                                                                                                                    â
   - anthropic_adapter.py: construct the client with a request timeout (anthropic.Anthropic(api_key=..., timeout=...)) and/or pass a per-call timeout to messages.create; map timeouts/APIError to PlannerRuntimeError with a clear message (extend classify_provider_error). Make the timeout configurable via settings (default e.g. 120 s).                                                                                                                                                                                                         â
   - base_agent_runtime.run_session: when a turn yields no tool calls and nothing was submitted, capture the model's final_text/reasoning so the failure reason is actionable rather than silent.
   - RunArchitectureUseCase.execute (src/app/planning/sessions/usecases.py): on the "no usable roadmap" path, include the model's final text in the failure reason so plan.architecture_failed carries why. Keep require_submit=False and the partial-output preservation.  â
   - Frontend already toasts plan.architecture_failed; ensure it offers a one-click retry (re-run architecture).

   A3 â Fix default model / thinking compatibility.                                                                                                                                                                                                                         â
   - Update default planner_model (defaults.py, models.py) and the runtime fallback (anthropic_planner_runtime.py _DEFAULT_MODEL) to a current, tool+thinking-capable model â recommend claude-sonnet-4-6 (confirm via claude-api skill).
   - Make thinking conditional: only send the thinking block when the configured model supports it (guard by model id, or a planner_thinking setting defaulting off for non-supporting models). Prevents hard-failure on any non-thinking model.

   A4 â Fix telemetry model: 'unknown'.                                                                                                                                                                                                                                     â
   - Expose the model on the planner/agent runtimes (e.g. a model property delegating to the adapter) and have runtime_wrappers.py read getattr(self._wrapped, "model", "unknown"), so llm.request/response carries the real model id.
                                                                                                                                                                                                                                                                            â
   A5 â Auto-start architecture drafting on approve-brief (backend-owned).
   - Extract the architecture-session launch in plan.py::run_architecture into a shared helper _launch_architecture_session(orchestrator) (guarded by registry.active("architecture") is None).                                                                             â
   - Call it from approve_brief after the discoveryâarchitecture transition succeeds, so drafting begins automatically and survives even if the UI is closed. Keep POST /plan/architecture/run for manual retry.
   - This fulfills the existing "starts architecture drafting" copy and removes the "I pressed draft and nothing happened" dependency on a separate click.                                                                                                                  â

   B. Verify + harden the post-approval JIT â execution chain
                                                                                                                                                                                                                                                                            â
   B1 â Confirm and test the chain approve-architecture â goal.unblocked â JIT (PlanGoalTasksUseCase) â task.created â task-manager â worker â task.completed, running embedded in the API process (dry-run: StubPlannerRuntime + SimulatedAgentRuntime).
   - Note/validate the dry-run event delivery wrinkle: in dry-run the RedisâSSE bridge is skipped (server.py:115); confirm embedded coordinators still receive goal.unblocked via the shared in-process event_port, and that task progress reaches the UI (or document the gap). Integration tests use fakeredis per the testing guide.                                                                                                                                                                                                             â
   - Apply the same observability (A1) to JIT runs so goal-population is visible, and surface orchestrator.jit_planning_failed to the operator.

   C. Frontend: one source of truth for gate readiness + auto-start UX

   C1 â Make Overview, GatePanel, and LifecycleRail agree. Architecture approval is offered only when the session has completed (completedRuns.includes('architecture'), driven by plan.architecture_completed, andâif C3âhydrated from backend).                           â
   - Overview.tsx:60-64: gate the architecture/phase_review rows on completion, not raw status; otherwise show a "drafting in progress / run architecture" row.
   - GatePanel.ArchitectureGate: disable/hide Approve unless ready; show "draftingâ¦" / failure+retry instead of an enabled "Approve all."                                                                                                                                   â

   C2 â Auto-start + live progress UX. On approve-brief success, reflect activeRun='architecture'; render the new progress SSE in the rail's live card; surface plan.architecture_failed with a Retry action.                                                               â

   C3 â Reload resilience (the "more resilience" ask). Add GET /plan/architecture/status (reuse registry + support.assemble_roadmap) returning { state: running|completed|failed|none, decisions, phases, error }; hydrate the store on load so a refresh mid/after-run keeps the correct gate. This also makes the E2E deterministic (poll status instead of racing SSE).                                                                                                                                                                             â

   D. Tests
                                                                                                                                                                                                                                                                            â
   D1 â Backend endpoint/integration tests (explicitly requested). Extend tests/integration/test_api_plan_run_sessions.py (dry-run, fakeredis, TestClient):
   - approve-brief auto-launches an architecture session; architecture/status reports completed; approve-architecture dispatches phase-1 goals.
   - Full chain assertion: after approve-architecture, goals get JIT tasks and tasks reach succeeded (embedded coordinators).
   - 409 guards: approving before completion; double-launch returnsÂ 409.
                                                                                                                                                                                                                                                                            â
   D2 â Unit tests: adapter timeoutâPlannerRuntimeError; thinking omitted for non-supporting model; telemetry surfaces real model; _launch_architecture_session idempotency.
                                                                                                                                                                                                                                                                            â
   D3 â E2E with Playwright (LAST, after AâC are green).
   - Install Playwright in frontend/ (@playwright/test), add playwright.config.ts, npm run test:e2e, and a harness that boots the API with AGENT_MODE=dry-run + an initialized temp project and the Vite dev server.                                                        â
   - Focused spec (the regression for this bug): discovery â approve brief â architecture auto-drafts (stub completes) â status completed â approve architecture â phase-1 goals populated with tasks â tasks execute. Assert no premature/duplicated approve and no danglingÂ 409.                                                                                                                                                                                                                                                            â

   Critical files

   - Backend run/observability: src/api/routers/plan.py, src/api/server.py (_wire_planner_sse_hook, coordinators), src/infra/logging/planner_logger.py, planner_callback.py, live_logger.py.
   - Runtime resilience/model: src/infra/runtime/planners/adapters/anthropic_adapter.py, base_agent_runtime.py, anthropic_planner_runtime.py, planner_factory.py, src/infra/settings/defaults.py, src/infra/settings/models.py, src/app/telemetry/runtime_wrappers.py.      â
   - Use cases: src/app/planning/sessions/usecases.py (Run/Approve Architecture), src/app/planning/sessions/support.py, src/app/orchestrator.py (JIT trigger), src/app/usecases/plan_goal_tasks.py.
   - Frontend: frontend/src/views/Overview.tsx, components/GatePanel.tsx, components/LifecycleRail.tsx, lib/queries.ts, lib/api.ts, store/plannerStore.ts.                                                                                                                  â
   - Tests: tests/integration/test_api_plan_run_sessions.py; new frontend/playwright.config.ts + frontend/e2e/architecture-flow.spec.ts.

   Verification                                                                                                                                                                                                                                                             â

   1. mypy src and ruff check src tests clean; pytest tests/unit tests/integration green (incl. new D1/D2).
   2. Manual (dry-run): AGENT_MODE=dry-run python -m src.infra.cli.main system api --port 8000 + npm run dev; approve brief â architecture auto-drafts with live logs in the rail â completes â approve â goals populate with tasks â tasks execute to completion. No 409   âdangling.
   3. Manual endpoint probe (the user's explicit ask): curl/httpx the architecture endpoints in dry-run â POST /api/plan/approve-brief, GET /api/plan/architecture/status, POST /api/plan/approve-architecture â asserting the documented transitions and 409 guards.       â
   4. npm run test:e2e passes the focused architecture-flow spec.