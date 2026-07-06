 Plan: Harden the Architecture Planning Layer (resilient sessions + typed roadmap)

   Context                                                                                   â

 The architecture planning phase is deterministically broken. The planner agent
   correctly proposes decisions and phases, then calls submit_architecture to finish â       â
 but the runtime never recognizes that call, burns all turns, and raises
 "Planning session exceeded max turns without submitting a roadmap". The session is
   marked FAILED and all proposed work is discarded (the duplicate phase proposals in        â
   the logs are the agent retrying because its submission is never acknowledged). Because
   the session never reaches COMPLETED, /approve-architecture then returns 409.
                                                                                             â
   Root cause (verified in code)

   BasePlannerRuntime detects the agent's "I'm done" signal by string-matching the tool      â
   name against a value hardcoded at construction:
   - anthropic_planner_runtime.py:59 â submit_tool_name="submit_final_roadmap"
   - base_agent_runtime.py:79 â if tool_call.name == self._submit_tool_name:                 â

   But the architecture session only exposes a tool named submit_architecture
   (architecture_tools.py:103), and phase-review exposes submit_review. Neither ever         â
   matches submit_final_roadmap, so submitted stays False forever. Only the tactical
   roadmap path (plan_goal_tasks) uses a tool literally named submit_final_roadmap,
   which is why that path works and the bug went unnoticed (the StubPlannerRuntime used in   â
   tests/dry-run also hardcodes submit_final_roadmap, masking it).

   Decisions taken (from user)                                                               â

   1. Model: Keep Phase; add a typed Roadmap artifact only â no new Milestone layer.
   2. Budget/interrupt behavior: Auto-finalize valid work. If â¥1 decision and â¥1 phase       â
   were proposed, treat the session as submitted and advance to the human approval gate.
   Only hard-fail when nothing usable was produced. Add user interruption.                   â
   3. Delivery: Phased. Stage 1 unblocks immediately; Stage 2 is the model refactor.
                                                                                             â
   Intended outcome

   Architecture (and phase-review) sessions reliably finish â by explicit submit, by
   exhausting the turn budget, or by user interrupt â and never throw away coherent proposed
   work. The roadmap becomes a typed, validated artifact (Phases â Goals + Decisions) insteadâ
 of an untyped dict, with a real validation feedback loop that helps the agent converge.

   ---                                                                                       â
 Stage 1 â Resilience (the unblock)
                                                                                             â
   1A. Flag-based terminal-tool detection (fixes the name-mismatch for every mode)
                                                                                             â
   Replace fragile name-matching with an explicit flag on the tool.

   - src/domain/ports/planner.py
     - Add terminal: bool = False to PlannerTool.                                            â
   - Add submitted: bool = False to PlannerOutput (did the agent explicitly submit).
   - Update the run_session abstract signature + docstring (new params below).
   - src/infra/runtime/planners/base_agent_runtime.py                                        â
     - Compute terminal_names = {t.name for t in tools if t.terminal}; if empty, fall back to
   {self._submit_tool_name} (preserves tactical-roadmap behavior).                           â
     - Replace the == self._submit_tool_name check (lineÂ 79) with in terminal_names.
   - Mark the finalize tools terminal=True:                                                  â
     - build_submit_architecture_tool (architecture_tools.py:102)
     - build_submit_review_tool (phase_review_tools.py)
     - the tactical submit_final_roadmap tool (in plan_goal_tasks tooling)
     - build_submit_project_brief_tool (discovery; for consistency)
                                                                                             â
 1B. Stop discarding work â auto-finalize on budget exhaustion

   - base_agent_runtime.py: add require_submit: bool = True. When the loop ends without an   â
   explicit submit: if require_submit â raise as today (keeps tactical/discovery strict);
   else return normally. Always set PlannerOutput.submitted accordingly. (Move artifact      â
   extraction so it only runs when there's something to extract.)
   - src/app/planning/sessions/usecases.py â RunArchitectureUseCase.execute:                 â
     - Call run_session(..., require_submit=False, max_turns=<configurable>, cancel_check=...).
     - After it returns, extract pending_decisions + pending_phases (existing                â
   support.extract_*). If both non-empty â session.complete(...) and return
   ArchitectureResult(..., needs_approval=True) regardless of whether the agent
   explicitly submitted. Else â session.fail("ended without a usable roadmap: need â¥1 decision and â¥1 phase") and return a failure result.
     - Keep the except PlannerRuntimeError arm for genuine provider/API errors â fail.       â
 - Apply the same auto-finalize shape to RunPhaseReviewUseCase.execute (usable = lessons
 present; next_phase stays optional for the final phase).
                                                                                             â
   1C. User interrupt / cancel (cooperative, checked between turns)
                                                                                             â
   - base_agent_runtime.py + port: add cancel_check: Optional[Callable[[], bool]] = None.
   At the top of each turn, if cancel_check and cancel_check(): break. A cancel breaks the   â
   loop â 1B auto-finalizes if valid, else fails with "cancelled by user".
   - Thread the signal API â runtime:
     - src/api/sessions.py: add cancel_requested: bool + request_cancel() to ApiSession;
   a registry helper to fetch the active session by kind (reuse active("architecture")).     â
     - src/api/routers/plan.py: new POST /api/plan/architecture/cancel â set the flag on the
   active architecture session (404 if none). In the existing background run()
   (plan.py:~217), pass cancel_check=lambda: session.cancel_requested down through           â
   orchestrator.run_architecture(...) â RunArchitectureUseCase.execute(cancel_check=...)
   â runtime.run_session(...).                                                               â
     - src/app/usecases/planner_orchestrator.py: thread cancel_check through run_architecture.
   - SSE: cancel + valid â existing plan.architecture_completed; cancel + nothing usable â   â
   plan.architecture_failed with a clear "cancelled" error. (Frontend already handles both.)
                                                                                             â
   1D. Configurable + larger budget, and a clearer prompt

   - Add planner_max_turns: int = 25 to MachineSettings (src/infra/settings/models.py),
   surfaced via SettingsContext. Wire it into the three Run use cases (constructor arg from  â
   container.py), replacing the hardcoded 15/20 at usecases.py:63,139,255.
   - src/app/planning/prompts/planning_prompt_builders.py â ArchitecturePromptBuilder.build:
   state the turn budget explicitly, instruct "propose each decision/phase once; do not      â
   re-propose identical items", "call submit_architecture exactly once when done", and note
   that if the budget is reached whatever has been proposed is sent to the human approval gatâ
   (so a coherent partial roadmap is acceptable). This directly reduces the wasted
   re-proposal turns seen in the logs.                                                       â

   1E. Keep dry-run / tests honest

   - StubPlannerRuntime (anthropic_planner_runtime.py:78): honor the new signature           â
   (require_submit, cancel_check, set submitted=True); call the terminal tool found in
   tools (via the terminal flag) instead of hardcoding submit_final_roadmap; when
   propose_phase_plan/propose_decision tools are present, call them with canned data first   â
 so architecture dry-runs populate session.roadmap_data and actually complete.
                                                                                             â
   Stage 1 tests
                                                                                             â
   - Unit tests/unit: terminal detection via flag; no raise when require_submit=False;
   cancel_check breaks the loop; submitted reflects reality.
   - Unit: RunArchitectureUseCase auto-finalizes when pending work exists after a no-submit
   run; fails when empty; same for phase review.                                             â
 - Integration tests/integration: end-to-end architecture run with a fake runtime that
 proposes phases+decisions then stops without submitting â session COMPLETED â
   /approve-architecture succeeds (proves the 409 is resolved).                              â
   - API: /architecture/cancel sets the flag and 404s when no active session.
                                                                                             â
   ---
   Stage 2 â Typed Roadmap artifact (model formalization, no Milestone)                      â

   Goal: replace the untyped session.roadmap_data dict + loose
   ArchitectureResult(pending_decisions, pending_phases) with one validated artifact.
                                                                                             â
 - New artifact ArchitectureRoadmap (frozen pydantic) in
 src/domain/value_objects/ â named to avoid colliding with the existing goal-DAG
   Roadmap in value_objects/goal.py:                                                         â
   phases: list[Phase]                 # ordered cyclical lifecycle units (existing Phase)
   decisions: list[DecisionEntry]                                                            â
   goal_specs: dict[str, GoalSpec]     # goal_name -> spec, replaces the loose goal_descriptions dict
   - Validation: non-empty phases; contiguous phase indices from current_phase_index; every
   goal_name referenced by a phase has a matching goal_specs entry; no duplicate goal names.
   - Centralize parsing: a RoadmapAssembler that builds + validates ArchitectureRoadmap      â
 from session.roadmap_data, replacing the scattered extract_pending_decisions /
 extract_pending_phases / find_goal_spec calls in support.py and usecases.py.
   Storage stays a dict (repo unchanged); typing happens at the boundary.                    â
   - Validation feedback loop: submit_architecture builds the ArchitectureRoadmap and,
   if invalid, returns {accepted: false, errors:[...]} with actionable messages (e.g.        â
   "goal 'foo' referenced by phase 0 has no description"). This gives the agent a concrete way
   to fix and finish â attacking the original "never converges" failure at its source.
   - Consume the type: ArchitectureResult carries roadmap: ArchitectureRoadmap;
   ApproveArchitectureUseCase reads roadmap.phases/decisions/goal_specs (drops the ad-hoc
   find_goal_spec lookup).                                                                   â
   - Surface it: extend src/api/schemas/plan.py (and frontend/src/types) so the
   architecture approval gate renders the full PhasesâGoals + Decisions structure; minor
   frontend work in the arch-gate component.                                                 â

   Keep all existing lifecycle transitions (approve_phase, trigger_review,                   â
   approve_phase_review) intact â the cyclical phase advance through plan.phases by
   current_phase_index already exists; Stage 2 only makes the up-front phase sequence a typedâ
   validated whole.

   ---
   Critical files
                                                                                             â
 - src/infra/runtime/planners/base_agent_runtime.py â loop, terminal detection, no-raise, cancel
   - src/infra/runtime/planners/anthropic_planner_runtime.py â Stub honesty + signatures     â
   - src/domain/ports/planner.py â PlannerTool.terminal, PlannerOutput.submitted, signature
   - src/app/planning/sessions/usecases.py â auto-finalize in Run{Architecture,PhaseReview}
   - src/app/planning/sessions/support.py â mark terminal tools; (StageÂ 2) RoadmapAssembler
   - src/app/planning/tools/architecture_tools.py / phase_review_tools.py â terminal=True; (S2) validation
   - src/app/planning/prompts/planning_prompt_builders.py â budget-aware architecture prompt
   - src/app/usecases/planner_orchestrator.py â thread cancel_check                          â
 - src/api/routers/plan.py + src/api/sessions.py â cancel endpoint + flag
 - src/infra/settings/models.py + src/infra/container.py â planner_max_turns wiring
 - (StageÂ 2) src/domain/value_objects/architecture_roadmap.py, src/api/schemas/plan.py, frontend/src/types

 Verification

 1. mypy src clean; ruff check src tests --fix; pytest tests/unit tests/integration.
 2. Dry-run end-to-end: AGENT_MODE=dry-run python -m src.infra.cli.main system start
 â architecture session reaches COMPLETED and /approve-architecture succeeds (noÂ 409).
 3. Live run: start API (python -m src.infra.cli.main system api --port 8000), drive an
 architecture run; confirm the agent's submit_architecture is acknowledged and the session
 completes in a few turns (no duplicate re-proposals, no max-turns failure).
 4. Budget path: force a low planner_max_turns and a non-submitting agent that still
 proposes â¥1 decision + â¥1 phase â session auto-finalizes to COMPLETED.
 5. Interrupt path: POST /api/plan/architecture/cancel mid-run â loop stops after the
 current turn, valid proposals are finalized (or a clean "cancelled" failure if none).  