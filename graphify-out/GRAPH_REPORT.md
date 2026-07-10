# Graph Report - .  (2026-07-10)

## Corpus Check
- 299 files · ~420,803 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3140 nodes · 7851 edges · 201 communities (171 shown, 30 thin omitted)
- Extraction: 76% EXTRACTED · 24% INFERRED · 0% AMBIGUOUS · INFERRED: 1910 edges (avg confidence: 0.64)
- Token cost: 264,093 input · 0 output

## Community Hubs (Navigation)
- Generated API Types
- Config & Reference API
- Frontend API Client
- UI Component Library
- React Router Vendor Core
- Goals Canvas & Phase Timeline
- Immer/Zustand Vendor
- App Shell & Chat Panel
- Outbox Relay & SSE Events
- TanStack Query Vendor Core
- Replan Use Case
- Plans API Router
- Plan Creation & Use-Case Tests
- Domain Error Hierarchy
- Agent Events & Task Results
- Encrypted Secret Store
- Git Workspace Port
- Provider & Model Catalog
- Reference Data Repositories
- Dummy Runner & Outbox Fakes
- React Router Components Vendor
- LLM Client Runtime
- Plan Edit Service
- Reasoner Config & CLI
- Navigation Scan & Aggregate Tests
- Pause/Resume Gate
- Fake Clock & In-Memory Repos
- Advance-Plan Dispatcher & Goal Entity
- Execution Handler Running Loop
- TanStack QueryClient Internals
- Planning Handler
- Reference Repos
- Test Conversation And Planning
- Test Transitions
- Readme
- Context
- Queries
- Chunk I2Mcd6Rr
- Test Runner Taxonomy
- @Tanstack React Query
- Support
- Reference Repos
- Activity
- Task
- Conversation
- Planner Orchestrator
- Test Api
- React Router Dom
- Test Worker Loop
- Tsconfig
- React Router Dom
- Engine
- Test Full Cycle
- Planning Errors
- Test Edge Cases
- Plan Repository
- Openai Reasoner
- Gatepanel
- @Tanstack React Query
- Gate Handler
- Test Reasoner Backoff
- 2026 07 02 Master Roadmap Final Fable 5
- Server
- Readme
- Agent Errors
- Test Openai Reasoner
- Test Agent Loop
- React Router Dom
- Agent Port
- Test Advance Plan
- @Tanstack React Query
- @Tanstack React Query
- Main
- Tables
- Execution Model
- Errors
- Dependency Checker
- @Tanstack React Query
- Request Logging
- Ports
- Chat Repository
- Planner Repo
- Test Full Cycle Llm
- Test Llm Client
- Chunk E55Nsntn
- React Router Dom
- React Router Dom
- Execution Handler
- Reasoner Port
- Agent Event Reader
- 2026 06 12 Code Review Remediation M1 M5
- Package
- React Router Dom
- Config
- Agent Repo
- Outbox
- Plan Lifecycle
- 2026 06 13 Api Stability Frontend Recove
- Readme
- Common
- Capability Repo
- Model Provider Repo
- Stub Reasoner
- Package
- @Tanstack React Query
- Readme
- Readme
- Exceptions
- Tasks Errors
- Agent Factory
- Base
- Error Handler
- 2026 07 03 Working Prototype Reasoner Fr
- Pre Refactor Backend
- Claude
- Agent Loop
- Package
- React Router Dom
- @Tanstack React Query
- Integration Guide
- Metrics
- Runner
- Fakes
- Control
- Capability Matching
- Zustand
- Readme
- Capability Factory
- Lifecycle
- Clock
- Taxonomy
- Test Chat Repository
- 2026 06 13 Architecture Session Hardenin
- React Router Dom
- Dependencies
- Engine
- Main
- Test Migrations
- Env
- Readme
- Navigation
- Main
- Container
- Vite Env D
- Favicon
- Init
- Init
- Init
- Readme
- Init
- Plans
- Plans
- Plans
- Plans
- Init
- Task
- Init
- Init
- Main
- Main
- Main
- Init
- Init
- Dummy Runner
- Pi Protocol
- Conftest
- Package
- Pyproject

## God Nodes (most connected - your core abstractions)
1. `AppContainer` - 115 edges
2. `Plan` - 110 edges
3. `Task` - 102 edges
4. `UnitOfWork` - 74 edges
5. `Goal` - 65 edges
6. `AgentSpec` - 55 edges
7. `env_factory()` - 49 edges
8. `IAModel` - 47 edges
9. `DummyBehavior` - 46 edges
10. `RetryPolicy` - 46 edges

## Surprising Connections (you probably didn't know these)
- `Orchestrator CI Workflow` --semantically_similar_to--> `Planned CI Pipeline (per-PR vs nightly split)`  [INFERRED] [semantically similar]
  .github/workflows/ci.yml → ROADMAP.md
- `Pause Gate (un-freeze #3)` --semantically_similar_to--> `Cooperative Pausing (between units, never mid-run)`  [INFERRED] [semantically similar]
  CLAUDE.md → backend/src/domain/aggregates/README.md
- `Dry-Run + Stub Default Mode` --semantically_similar_to--> `In-Memory Fakes (testing/fakes.py)`  [INFERRED] [semantically similar]
  README.md → backend/src/app/README.md
- `Orchestrator CI Workflow` --references--> `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml → README.md
- `Orchestrator CI Workflow` --conceptually_related_to--> `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml → ROADMAP.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Phase handlers behind the PhaseHandler protocol (dispatched by PlanDispatcher)** — backend_src_app_readme_plandispatcher, backend_src_app_readme_executionhandler, backend_src_app_readme_planninghandler, backend_src_app_readme_gatehandler [EXTRACTED 1.00]
- **Transactional durability and crash-recovery guarantees proven by the truth test** — backend_docs_integration_guide_version_cas, claude_transactional_outbox, backend_src_domain_repositories_readme_lease, backend_src_app_readme_crash_safety_choreography, backend_docs_integration_guide_truth_test [INFERRED 0.85]
- **Conversational planning flow through the Reasoner port** — backend_docs_integration_guide_reasoner_port, backend_docs_integration_guide_stubreasoner, backend_docs_integration_guide_openaireasoner, backend_src_app_readme_conversation_use_cases, backend_src_app_readme_planninghandler, backend_docs_integration_guide_enriching_jit [EXTRACTED 1.00]
- **Durable Availability-Gate Pattern (persisted timestamp/flag honored by the claim/scan predicate)** — docs_architecture_execution_model_pause_gate, docs_decisions_decision_log_planning_backoff_gate, docs_architecture_execution_model_pull_scan [EXTRACTED 1.00]
- **Persist-First Crash-Safe Core (document + CAS + UoW + outbox + lease, proven by the truth test)** — docs_architecture_data_model_plan_as_document, docs_architecture_data_model_version_cas, docs_architecture_data_model_unit_of_work, docs_architecture_events_and_observability_transactional_outbox, docs_decisions_adr_001_concurrency_lease_per_plan_lease, backend_tests_readme_truth_test [EXTRACTED 1.00]
- **Two Streams, One Delivery Path (outbox + agent_events → relay → SSE → frontend bridge)** — docs_architecture_events_and_observability_transactional_outbox, docs_architecture_events_and_observability_agent_events_stream, docs_architecture_events_and_observability_outbox_relay, docs_architecture_frontend_sse_bridge [EXTRACTED 1.00]
- **Architecture-phase 409 Dead-end Remediation Arc** — docs_history_planning_2026_06_13_api_stability_frontend_recovery_opus_4_8_architecture_run_endpoints, docs_history_planning_2026_06_13_architecture_session_hardening_opus_4_8_submit_tool_name_mismatch, docs_history_planning_2026_06_13_architecture_session_hardening_opus_4_8_auto_finalize, docs_history_planning_2026_06_14_architecture_phase_fix_opus_4_8_auto_start_architecture, docs_history_planning_2026_06_14_architecture_phase_fix_opus_4_8_gate_desync, docs_history_planning_2026_06_15_playwright_e2e_plan_deferred_architecture_flow_spec [EXTRACTED 1.00]
- **Pre-refactor Capabilities Replaced by the Nine-phase Machine** — docs_history_pre_refactor_architecture_old_plan_lifecycle, docs_legacy_pre_refactor_backend_planner_sessions, docs_history_pre_refactor_architecture_reconciler, docs_legacy_pre_refactor_backend_redis_event_topology, docs_history_planning_2026_07_02_master_roadmap_final_fable_5_nine_phase_machine, docs_history_planning_2026_07_02_master_roadmap_final_fable_5_per_plan_lease [EXTRACTED 1.00]
- **Working-prototype Reasoner Design Stack** — docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_two_method_reasoner_port, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_tool_calling_agent_loop, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_openaireasoner, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_stubreasoner, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_jit_enriching, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_architecture_passthrough, docs_history_planning_2026_07_03_working_prototype_reasoner_frontend_fable_5_multi_turn_chat_commit [EXTRACTED 1.00]

## Communities (201 total, 30 thin omitted)

### Community 0 - "Generated API Types"
Cohesion: 0.02
Nodes (205): ClientOptions, ConfigDeleteValueData, ConfigDeleteValueError, ConfigDeleteValueErrors, ConfigDeleteValueResponse, ConfigDeleteValueResponses, ConfigGetScopeData, ConfigGetScopeError (+197 more)

### Community 1 - "Config & Reference API"
Cohesion: 0.06
Nodes (61): ConfigValue, delete_value(), get_scope(), BaseModel, /api/config — the two-tier config store (roadmap 2.8): scope 'orchestrator' for, set_value(), get_plan(), list_plans() (+53 more)

### Community 2 - "Frontend API Client"
Cohesion: 0.08
Nodes (64): AgentEventRow, API_TOKEN, applyEdit(), approvePlan(), createAgent(), createCapability(), createModel(), createPlan() (+56 more)

### Community 3 - "UI Component Library"
Cohesion: 0.07
Nodes (51): Button, ButtonProps, Size, Variant, Card(), ConfirmAction(), Dialog(), Field() (+43 more)

### Community 4 - "React Router Vendor Core"
Cohesion: 0.05
Nodes (41): convertDataStrategyResultToDataResult(), DefaultErrorComponent(), findNearestBoundary(), findRedirect(), getActionDataForCommit(), getDoneFetcher(), getLoaderMatchesUntilBoundary(), getMatchesToLoad() (+33 more)

### Community 5 - "Goals Canvas & Phase Timeline"
Cohesion: 0.07
Nodes (44): GoalGroupNode(), KIND_COLOR, PhaseTimeline(), WALK, KIND_COLOR, nodeTypes, NOTE: no `= []` defaults here — a fresh array per render would change, metaFor() (+36 more)

### Community 6 - "Immer/Zustand Vendor"
Cohesion: 0.12
Nodes (52): applyPatches(), constructor(), createDraft(), createProxy(), createProxyProxy(), createScope(), current(), currentImpl() (+44 more)

### Community 7 - "App Shell & Chat Panel"
Cohesion: 0.09
Nodes (38): App(), PlanShell(), StaleNotice(), ChatPanel(), MODE_HINTS, ConsoleDock(), lineColor(), DetailPanel() (+30 more)

### Community 8 - "Outbox Relay & SSE Events"
Cohesion: 0.06
Nodes (35): AbstractEventLoop, Session, sessionmaker, src/api/outbox_relay.py — delivers outbox rows to their consumers (roadmap 4.4)., One relay pass. Returns (rows delivered, new agent-events cursor)., Thread body: poll until told to stop. Own connections throughout —     never tou, relay_once(), run_outbox_relay() (+27 more)

### Community 9 - "TanStack Query Vendor Core"
Cohesion: 0.05
Nodes (22): fetchInfiniteQuery(), getCurrentQuery(), getPreviousPageParam(), getQueries(), getResult(), hashKey(), hasObjectPrototype(), hasPreviousPage() (+14 more)

### Community 10 - "Replan Use Case"
Cohesion: 0.07
Nodes (35): Chat-triggered mid-RUNNING replan: skip pending work -> REPLANNING., replan_mid_running(), Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING., review_replan(), request_replan — enter the conversational re-plan (state machinery only).  Two e, request_replan(), Plan, BaseModel (+27 more)

### Community 11 - "Plans API Router"
Cohesion: 0.16
Nodes (45): agent_events(), AgentEventResponse, chat_history(), ChatMessageResponse, create(), CreatePlanRequest, discovery(), edit_plan() (+37 more)

### Community 12 - "Plan Creation & Use-Case Tests"
Cohesion: 0.12
Nodes (46): create_plan(), RetryPolicy, create_plan — entry point that turns a brief into a persisted Plan.  Idempotent, Create a plan from a brief. Returns the plan id. Idempotent on request_id., catalogs(), edit(), make_agent(), _paused_running_plan() (+38 more)

### Community 13 - "Domain Error Hierarchy"
Cohesion: 0.07
Nodes (33): BaseAppException, DomainError, Any, Exception, Base class for all domain-rule violations., Common root for every typed application error.      Subclasses set a class-level, EntityAlreadyExistsError, ModelNotFoundError (+25 more)

### Community 14 - "Agent Events & Task Results"
Cohesion: 0.06
Nodes (23): ABC, Task, TaskResult, AgentEvent, BaseModel, Fine-grained agent runtime events — tool calls, steps, tokens streamed by the pi, BaseModel, Typed output of a task run, and the idempotency record: if set, the work     alr (+15 more)

### Community 15 - "Encrypted Secret Store"
Cohesion: 0.07
Nodes (33): Canonical ref for a provider's API key., Session, sessionmaker, src/infra/db/secret_store.py — the SQLite secret store (envelope encryption).  E, Internal infra helper: the single place plaintext is unwrapped.          Used by, SqliteSecretStore, SecretTable, Infrastructure-layer errors.  The domain owns DomainError (business-rule violati (+25 more)

### Community 16 - "Git Workspace Port"
Cohesion: 0.08
Nodes (23): Protocol, The workspace port: where a task attempt's file changes live.  The git adapter m, Git-branching seam. NoOp now (handle.path = shared dir); git adapter later     m, Workspace, WorkspaceHandle, _git(), _git_ok(), GitBranchWorkspace (+15 more)

### Community 17 - "Provider & Model Catalog"
Cohesion: 0.08
Nodes (19): IAModel, BaseModel, ModelProvider, BaseModel, ModelFactory, ProviderFactory, Any, ModelProvider (+11 more)

### Community 18 - "Reference Data Repositories"
Cohesion: 0.12
Nodes (25): CapabilityNotFoundError, Delete-guard: refuse to delete reference data still in use by something active., ReferencedEntityInUseError, _capability_from_row(), AgentSpec, Capability, src/infra/db/reference_repos.py — SQLite reference-data repositories.  Implement, Non-raising read of the default-agent marker (API status reads). (+17 more)

### Community 19 - "Dummy Runner & Outbox Fakes"
Cohesion: 0.10
Nodes (23): DummyAgentRunner, DummyBehavior, InMemoryOutbox, InMemoryUnitOfWork, How the dummy should behave for a given task id., Implements AgentRunner with no LLM/subprocess. Scriptable per task id so     tes, AgentSpec, BaseModel (+15 more)

### Community 20 - "React Router Components Vendor"
Cohesion: 0.09
Nodes (36): react, Await(), convertRouteMatchToUiMatch(), createMemoryHistory(), createMemoryRouter(), createRoutesFromChildren(), DataRoutes(), _extends2() (+28 more)

### Community 21 - "LLM Client Runtime"
Cohesion: 0.10
Nodes (25): AssistantTurn, LLMClient, OpenAIChatClient, Any, BaseModel, Protocol, The OpenAI-compatible chat client (async) — the old adapter's request layer.  LL, Normalize the provider's token usage into prompt/completion/total.         Retur (+17 more)

### Community 22 - "Plan Edit Service"
Cohesion: 0.13
Nodes (32): Any, _require(), InvalidEditError, GoalAlreadyRunningError, Edit/mutation rejected because the goal is already running or finished., add_task(), _assert_acyclic(), _assert_editable() (+24 more)

### Community 23 - "Reasoner Config & CLI"
Cohesion: 0.09
Nodes (29): BaseModel, /api/reasoner — reasoner configuration status.  `GET /reasoner/status` re-runs t, reasoner_status(), ReasonerStatusResponse, cli(), AIPOM agent orchestrator., Catalog-resolved: config key reasoner.mode selects stub (default,         no sec, load_master_key() (+21 more)

### Community 24 - "Navigation Scan & Aggregate Tests"
Cohesion: 0.18
Nodes (33): next_action(), NextAction, Derive the next actionable unit by scanning statuses at time `now`.      `now` i, exec_plan(), goal(), Aggregate orchestration, navigation, edits, binding, factories — the behaviors t, Un-freeze #3: a terminal task failure pauses the plan (goal stays open,     phas, task() (+25 more)

### Community 25 - "Pause/Resume Gate"
Cohesion: 0.13
Nodes (30): Clear the pause gate and requeue failed work (the manual retry): FAILED     task, resume(), pause_plan(), pause/resume — the human pause gate and the manual retry (un-freeze #3).  Pause, Human pause command. Idempotent: pausing an already-paused plan is a     no-op (, Human resume command = the manual retry. Raises InvalidTransitionError     (422), resume_plan(), env_factory() (+22 more)

### Community 26 - "Fake Clock & In-Memory Repos"
Cohesion: 0.11
Nodes (13): _Claim, FakeClock, _Handle, InMemoryPlanRepository, NoOpWorkspace, datetime, Plan, In-memory test doubles for the application layer. Let advance_plan be tested end (+5 more)

### Community 27 - "Advance-Plan Dispatcher & Goal Entity"
Cohesion: 0.09
Nodes (22): advance_plan(), advance_plan — thin phase DISPATCHER (one unit of work).  Routes on plan.phase t, Backwards-compatible entry point. Builds a dispatcher and delegates. Returns, Goal, BaseModel, Status, Phase-level chunk owning an ordered task list. Guarded self-transitions,     cal, Close the goal as SKIPPED (its iteration was abandoned by a replan).         All (+14 more)

### Community 28 - "Execution Handler Running Loop"
Cohesion: 0.13
Nodes (24): ExecutionHandler, TaskResult, ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.  This is the, If the plan left RUNNING (mid-flight replan), return the task for the         to, Plain values captured inside txn1 — never live aggregate refs across the     tra, _Unit, DomainEvent, BaseModel (+16 more)

### Community 29 - "TanStack QueryClient Internals"
Cohesion: 0.12
Nodes (31): add(), build(), defaultQueryOptions(), difference(), fetchOptimistic(), functionalUpdate(), get(), getCurrentResult() (+23 more)

### Community 30 - "Planning Handler"
Cohesion: 0.13
Nodes (21): Enum, str, Phase handlers: one concern per phase.  advance_plan is a thin DISPATCHER that r, What one advance step tells the worker loop to do next., Signal, _next_unenriched(), PlanningHandler, Goal (+13 more)

### Community 31 - "Reference Repos"
Cohesion: 0.09
Nodes (14): Session, sessionmaker, ProjectDefinition, Repo-level default-agent marker (not part of AgentSpec)., SqliteProjectRepository, _is_locked(), Session, sessionmaker (+6 more)

### Community 32 - "Test Conversation And Planning"
Cohesion: 0.19
Nodes (25): _drive_planning(), goal(), handler(), plan_in(), Plan, PlanPhase, Task, The conversational phases (multi-turn chat with commit) and the worker-driven pl (+17 more)

### Community 33 - "Test Transitions"
Cohesion: 0.15
Nodes (24): Arm the pause gate: the claim predicate skips a paused plan, so the         work, InvalidTransitionError, A state transition was attempted that the current status does not allow., mk_goal(), mk_task(), Exhaustive state-transition tests — the guarantee against transition bugs., test_goal_illegal_transition_raises(), test_goal_lifecycle() (+16 more)

### Community 34 - "Readme"
Cohesion: 0.15
Nodes (26): INTEGRATION_GUIDE.md (frozen port contracts), Tests README, Dual-Backend Truth Test, Plan-as-Document (one JSON document in plans.data), Events & Observability, agent_events Telemetry Stream, Outbox Relay → SSE Delivery, Transactional Outbox (+18 more)

### Community 35 - "Context"
Cohesion: 0.12
Nodes (23): Capability, Goal, Plan, Task, Plan -> markdown context for the reasoner prompts (the old PlanningContextRender, The capability catalog as markdown — the ONLY ids task     required_capabilities, render_capabilities(), _render_live_goal() (+15 more)

### Community 36 - "Queries"
Cohesion: 0.12
Nodes (17): useConfigScope(), useDefaultAgent(), useModels(), useProviders(), useReasonerStatus(), useRunnerStatus(), useSetConfigKey(), RunnerAgentStatus (+9 more)

### Community 37 - "Chunk I2Mcd6Rr"
Cohesion: 0.08
Nodes (21): NOTE: if you add a camelCased prop to this list,, NOTE: if you add a camelCased prop to this list,, NOTE: if you add a camelCased prop to this list,, TODO: When we delete legacy mode, we should make this error argument, TODO: Remove this dead flag, TODO: Remove this dead flag, TODO: Remove outdated deferRenderPhaseUpdateToNextBatch experiment. We, NOTE: This will not work correctly for non-generic events such as `change`, (+13 more)

### Community 38 - "Test Runner Taxonomy"
Cohesion: 0.17
Nodes (19): Raised by an AgentRunner when a task run fails. Carries a human-readable     `re, TaskFailed, CollectingEventSink, FailureKind, Typed classification of a task failure. Produced by the agent runner,     consum, LocalDirHandle, make_cli(), Path (+11 more)

### Community 39 - "@Tanstack React Query"
Cohesion: 0.13
Nodes (25): bindMethods(), canFetch(), canRun(), constructor(), continue(), createRetryer(), execute(), fetchState() (+17 more)

### Community 40 - "Support"
Cohesion: 0.13
Nodes (16): One UoW per worker/request — the instance is not thread-safe., src/infra/db/unit_of_work.py — SqliteUnitOfWork (the UnitOfWork port).  RE-ENTER, SqliteUnitOfWork, The assembled execution stack: drive_plan on the REAL SQLite UoW + the REAL git-, Dummy that actually writes a file into the workspace so the git flow has     som, test_full_stack_drive_success_and_rollback(), WritingDummyRunner, test_agent_delete_allowed_when_plan_terminal() (+8 more)

### Community 41 - "Reference Repos"
Cohesion: 0.15
Nodes (10): _guard_model_in_use(), ModelProvider, Session, sessionmaker, Guard-up: a model referenced by config (the model_role tier mapping)     or boun, The entity owns its models: make the model rows match provider.models., Prototype-grade reference scan over non-terminal plan JSON documents., _referenced_by_active_plan() (+2 more)

### Community 42 - "Activity"
Cohesion: 0.16
Nodes (17): ConnectionIndicator(), ThemeToggle(), queryClient, useMetrics(), applyTheme(), currentTheme(), getInitialTheme(), Theme (+9 more)

### Community 43 - "Task"
Cohesion: 0.09
Nodes (11): datetime, FailureKind, Status, TaskResult, True if the task is eligible to run at `now` — i.e. not gated by an         unex, Return to PENDING for retry. Result cleared; attempts preserved.         `not_be, Mark the task terminal-SKIPPED without running it (work became         unnecessa, Terminal-skip an in-flight task whose iteration was abandoned by a         repla (+3 more)

### Community 44 - "Conversation"
Cohesion: 0.15
Nodes (17): ChatStore, Per-plan conversation history (DISCOVERY / REPLANNING). Writes run     OUTSIDE t, _conversation_turn(), ConversationResult, discovery_message(), BaseModel, PlanPhase, conversation — the chat-driven phases (the driver model's third driver).  DISCOV (+9 more)

### Community 45 - "Planner Orchestrator"
Cohesion: 0.14
Nodes (9): datetime, FailureKind, NextAction, Task, TaskResult, Tolerant finalize: terminal-skip an in-flight task whose iteration was         a, Human-driven redo of a DONE task (review gate). The invariant (only a         DO, A TRANSIENT reasoner failure in a worker-driven planning phase: bump the (+1 more)

### Community 46 - "Test Api"
Cohesion: 0.10
Nodes (8): _plan_at_awaiting_review(), The thin API over TestClient: the plan lifecycle through HTTP, the error-> HTTP, Write agent_events rows directly through the sink for read-side tests., Drive a stub plan discovery->architecture->enriching->awaiting_review by     com, _seed_agent_events(), test_agent_events_read_endpoint(), test_metrics_endpoint(), test_pause_resume_and_edit_over_http()

### Community 47 - "React Router Dom"
Cohesion: 0.13
Nodes (22): callDataStrategyImpl(), callLoaderOrAction(), convertRoutesToDataRoutes(), createBrowserHistory(), createBrowserRouter(), createHashHistory(), createHashRouter(), createRouter() (+14 more)

### Community 48 - "Test Worker Loop"
Cohesion: 0.13
Nodes (19): run_worker — the orchestrator loop (Option A: loop-driven, pull-based).  This is, One claim-and-drive cycle. Returns True only if actual work ADVANCED —     not m, worker_tick(), plan_with_chain(), Worker-loop tests: the full claim->drive->release cycle, crash recovery via leas, Only ARCHITECTURE / ENRICHING / RUNNING are worker-claimable. Conversational, Regression for the hot claim->release spin: a claimable plan whose only     work, Two goals where g2 depends on g1 — the classic case that produced     pending-no (+11 more)

### Community 49 - "Tsconfig"
Cohesion: 0.10
Nodes (20): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleResolution (+12 more)

### Community 50 - "React Router Dom"
Cohesion: 0.18
Nodes (21): createPath(), createSearchParams(), getInvalidPathError(), getPathContributingMatches(), getResolveToMatches(), getSearchParamsForLocation(), getTargetMatch(), hasNakedIndexQuery() (+13 more)

### Community 51 - "Engine"
Cohesion: 0.14
Nodes (16): InMemoryChatStore, Mirrors SqliteChatRepository: per-plan append-only history, insertion     order, _apply_pragmas(), build_engine(), make_session_factory(), Any, Engine, Session (+8 more)

### Community 52 - "Test Full Cycle"
Cohesion: 0.23
Nodes (14): Human approval at the pre-execution gate: advance into execution., resume_from_review(), THE FULL CYCLE on the real stack (SQLite UoW + stub reasoner + dummy runner):, Tick until the worker finds nothing to progress (gates/conversational         ph, The user requests a replan WHILE a task is executing; the late failure     termi, The container-wired entrypoint: boots on an empty db, idles (sleeps, no     spin, Gate chat-back (un-freeze #3): at the pre-execution gate the user asks to     re, Pause/resume with editing while paused, and the auto-pause recovery loop:     ap (+6 more)

### Community 53 - "Planning Errors"
Cohesion: 0.13
Nodes (12): EmptyPlanError, PlanAlreadyTerminalError, Operation rejected because the plan is already DONE or FAILED., A plan must have a brief / cannot be created empty., PlanFactory, Any, Plan, RetryPolicy (+4 more)

### Community 54 - "Test Edge Cases"
Cohesion: 0.22
Nodes (18): drive_plan(), Advance one plan until it stops making progress. Returns (terminal signal,     u, agent(), drive(), harness(), Edge-case hardening: degenerate plans, terminal-state guards, multi-failure sequ, test_advance_already_done_plan_returns_done(), test_advance_already_failed_plan_returns_failed() (+10 more)

### Community 55 - "Plan Repository"
Cohesion: 0.15
Nodes (6): Plan, Session, sessionmaker, src/infra/db/plan_repository.py — SqlitePlanRepository (the PlanRepository port), Cheap listing off the promoted columns — no document parsing., SqlitePlanRepository

### Community 56 - "Openai Reasoner"
Cohesion: 0.15
Nodes (10): _build_task(), Any, Capability, ConversationMode, Goal, Plan, Task, src/infra/reasoner/openai_reasoner.py — the real Reasoner (OpenAI-compatible). (+2 more)

### Community 57 - "Gatepanel"
Cohesion: 0.16
Nodes (15): PostExecutionGate(), PreExecutionGate(), RoadmapEditor(), ICONS, Toaster(), useApprovePlan(), useFinishReview(), usePlanCommand() (+7 more)

### Community 58 - "@Tanstack React Query"
Cohesion: 0.14
Nodes (19): defaultMutationOptions(), find(), findAll(), getMutationDefaults(), getObserversCount(), getQueriesData(), invalidate(), invalidateQueries() (+11 more)

### Community 59 - "Gate Handler"
Cohesion: 0.16
Nodes (11): PhaseHandler, Plan, Protocol, Handles one advance step for the phase(s) it owns. Given the plan_id and the, GateHandler, Plan, GateHandler — owns the human-gate phases (AWAITING_REVIEW, REVIEW).  A gate paus, Transaction boundary. Owns a PlanRepository and an Outbox; entering starts     a (+3 more)

### Community 60 - "Test Reasoner Backoff"
Cohesion: 0.21
Nodes (14): enriching_plan(), FailingReasoner, goal(), handler(), Plan, Task, Reasoner-failure handling in the worker-driven planning phases (un-freeze #2): a, The gate is durable: an armed plan is not claimed until the clock passes it — (+6 more)

### Community 61 - "2026 07 02 Master Roadmap Final Fable 5"
Cohesion: 0.16
Nodes (18): Redis PEL Recovery (XAUTOCLAIM + startup replay), Master Roadmap (FINAL) - Clean Domain to Launch, Abandoned-iteration Rule / Tolerant Finalize, Append-only Replan Loop with Iteration Counter, Domain Freeze after Phase 0, Driver Model / Worker Claim Predicate, Integration Truth Test (real-SQLite re-run), Nine-phase Plan Machine (+10 more)

### Community 62 - "Server"
Cohesion: 0.15
Nodes (14): APIRoute, main(), scripts/export_openapi.py — Dump the FastAPI OpenAPI schema to a file.  Used by, _cors_origins(), create_app(), FastAPI, src/api/server.py — FastAPI application factory (the thin API).  Responsibilitie, Frontend origins allowed to read the API (incl. the SSE stream).     Defaults co (+6 more)

### Community 63 - "Readme"
Cohesion: 0.17
Nodes (16): Crash-Safety Choreography (ExecutionHandler rules), ExecutionHandler (RUNNING pull-scan loop), Plan Aggregate Root, Append-Only Replan Loop, AgentSpec Entity (role + model_role tiers), Goal Entity (status derived from tasks), Task Entity (guarded self-transitions + backoff gate), Delete-Guard Integrity Rule (cascade down, guard up) (+8 more)

### Community 64 - "Agent Errors"
Cohesion: 0.18
Nodes (7): InMemoryAgentRepository, AgentSpec, AgentNotFoundError, CapabilityNoLongerSatisfiedError, NoDefaultAgentError, A task references an agent id that no longer exists (e.g. user deleted it)., The bound agent no longer covers the task's required capabilities (user     edit

### Community 65 - "Test Openai Reasoner"
Cohesion: 0.39
Nodes (15): OpenAIReasoner, FakeLLMClient, converse(), make_plan(), msg(), OpenAIReasoner on the FakeLLMClient: ask vs commit turns, history replay as plai, test_converse_emits_llm_call_with_summed_usage(), test_enrich_emits_llm_call_and_missing_usage_is_zero() (+7 more)

### Community 66 - "Test Agent Loop"
Cohesion: 0.30
Nodes (14): FakeLLMClient — a scripted LLMClient for driving the agent loop and the OpenAIRe, text_turn(), tool_turn(), The tool-calling agent loop: terminal accept, {accepted:false} self-correction,, The self-correction loop: first submit rejected with errors, the model     sees, run(), submit_tool(), test_budget_exhaustion_raises_transient() (+6 more)

### Community 67 - "React Router Dom"
Cohesion: 0.18
Nodes (16): getDataRouterConsoleError2(), invariant(), normalizeRedirectLocation(), Route(), ScrollRestoration(), stripBasename(), useDataRouterContext2(), useDataRouterState2() (+8 more)

### Community 68 - "Agent Port"
Cohesion: 0.14
Nodes (11): AgentRunner, AgentSpec, Protocol, Task, TaskResult, The agent-execution port: run ONE task, return a result.  Failures are signaled, Executes ONE task and returns a result (or raises TaskFailed). Knows     NOTHING, AgentEventSink (+3 more)

### Community 69 - "Test Advance Plan"
Cohesion: 0.29
Nodes (14): make_plan(), End-to-end orchestration tests for advance_plan. Runs against the in-memory doub, Simulate a crash after the agent ran but before txn2: the task is RUNNING     wi, Drive the plan through RUNNING. Execution exhausts into the REVIEW gate     (pau, run_to_completion(), test_agent_events_streamed_and_tagged(), test_check_before_act_skips_completed_task(), test_missing_agent_raises_before_running() (+6 more)

### Community 70 - "@Tanstack React Query"
Cohesion: 0.20
Nodes (15): addObserver(), clear(), clearGcTimeout(), clearTimeout(), destroy(), isValidTimeout(), notify(), onSubscribe() (+7 more)

### Community 71 - "@Tanstack React Query"
Cohesion: 0.24
Nodes (15): createResult(), hasListeners(), isStale(), onQueryUpdate(), resolveQueryBoolean(), setOptions(), shallowEqualObjects(), shouldFetchOn() (+7 more)

### Community 72 - "Main"
Cohesion: 0.23
Nodes (11): ok(), config_get(), config_list(), config_set(), db_upgrade(), plan_list(), plan_show(), src/infra/cli/main.py — the orchestrate CLI (fundamental commands only, roadmap (+3 more)

### Community 73 - "Tables"
Cohesion: 0.21
Nodes (11): AgentEventTable, Base, ConfigTable, OutboxTable, PlanChatMessageTable, PlanRequestTable, PlanTable, src/infra/db/tables.py — SQLAlchemy table definitions for the orchestrator DB. (+3 more)

### Community 74 - "Execution Model"
Cohesion: 0.24
Nodes (14): Infrastructure Layer README, Data Model (SQLite schema, plan-as-document, secrets), Envelope-Encrypted Secrets, Re-enterable SqliteUnitOfWork, Version CAS (optimistic concurrency), Execution Model (worker, lease, crash choreography, workspace), Git-Branching Workspace as Rollback, Pull-Scan Navigation (next_action) (+6 more)

### Community 75 - "Errors"
Cohesion: 0.18
Nodes (12): classify_provider_error(), _extract_provider_error_text(), provider_error_from_empty_choices(), Exception, Reasoner runtime errors + provider-error classification.  `transient` marks a fa, The planning LLM runtime could not produce a usable turn/artifact.      Subclass, Translate a raw provider API error into an actionable ReasonerError.      A tool, Build a ReasonerError for a 200 response that carries no choices.      Some Open (+4 more)

### Community 76 - "Dependency Checker"
Cohesion: 0.19
Nodes (9): check_dependencies(), DependencyReport, DepResult, _probe_binary(), src/infra/runtime/dependency_checker.py — probes the external tools the real age, Probe git + every CLI runtime binary. Never raises., Result of a single dependency probe., Aggregated result of all dependency probes. (+1 more)

### Community 77 - "@Tanstack React Query"
Cohesion: 0.19
Nodes (14): cancel(), cancelQueries(), dehydrate(), dehydrateMutation(), dehydrateQuery(), getDefaultOptions(), getMutationCache(), hydrate() (+6 more)

### Community 78 - "Request Logging"
Cohesion: 0.17
Nodes (11): _envelope(), get_request_id(), Request, src/api/middleware/request_logging.py — correlation id + request lifecycle logs., Return the current request's correlation id, or '-' outside a request., Bind a correlation id on the current context (e.g. background work)., _request_logging_enabled(), RequestLoggingMiddleware (+3 more)

### Community 79 - "Ports"
Cohesion: 0.15
Nodes (8): Outbox, Exception, FailureKind, Protocol, Application-layer ports + re-exports of the domain ports.  The five execution/si, Raised by a Reasoner when it cannot produce a usable turn/artifact — the     pla, Coarse domain events, added INSIDE the state transaction (transactional     outb, ReasonerUnavailable

### Community 80 - "Chat Repository"
Cohesion: 0.18
Nodes (6): ChatMessage, One turn of a plan's DISCOVERY/REPLANNING conversation. Persisted by the     Cha, Session, sessionmaker, src/infra/db/chat_repository.py — SqliteChatRepository (the ChatStore port).  Co, SqliteChatRepository

### Community 81 - "Planner Repo"
Cohesion: 0.18
Nodes (4): PlanRepository, Plan, Protocol, Single source of truth for plan persistence + the concurrency primitives.      T

### Community 82 - "Test Full Cycle Llm"
Cohesion: 0.23
Nodes (7): PlanPhase, Enum, str, The nine-phase machine (see MASTER_ROADMAP_FINAL.md):      DISCOVERY    — the fi, LLMStack, THE FULL CYCLE driven by the REAL reasoner implementation (OpenAIReasoner) on a, test_full_cycle_on_the_real_reasoner_with_scripted_llm()

### Community 83 - "Test Llm Client"
Cohesion: 0.37
Nodes (12): api_error(), assistant_message(), make_client(), OpenAIChatClient request behavior: transient retry with backoff, permanent fail-, An OpenAIChatClient whose chat.completions.create pops `responses`     (an Excep, response_with(), test_empty_choices_is_transient_and_retried(), test_malformed_tool_arguments_parse_to_empty_dict() (+4 more)

### Community 85 - "React Router Dom"
Cohesion: 0.22
Nodes (13): BrowserRouter(), flushSyncSafe(), generatePath(), getFormEncType(), HashRouter(), HistoryRouter(), logV6DeprecationWarnings(), MemoryRouter() (+5 more)

### Community 86 - "React Router Dom"
Cohesion: 0.19
Nodes (13): convertFormDataToSearchParams(), convertSearchParamsToFormData(), createClientSideRequest(), createKey(), createLocation(), isMutationMethod(), isSubmissionNavigation(), isValidMethod() (+5 more)

### Community 87 - "Execution Handler"
Cohesion: 0.30
Nodes (6): Goal, Plan, Task, Scan exhausted: RUNNING -> REVIEW (post-exec gate), then pause for the         h, Goal-failure policy (amended by un-freeze #3): a goal whose remaining         wo, Check-before-act idempotency: the work already happened (crash between         a

### Community 88 - "Reasoner Port"
Cohesion: 0.17
Nodes (9): BaseModel, Capability, ConversationMode, Goal, Plan, Task, The reasoner port: the planning LLM behind the phase machine.  Two methods, matc, One converse() turn. goals=None means "still conversing" (the message is     a q (+1 more)

### Community 89 - "Agent Event Reader"
Cohesion: 0.18
Nodes (7): Any, Session, sessionmaker, src/infra/db/agent_event_reader.py — the read side of the agent_events stream., Global (or per-plan) roll-up: LLM sessions/tokens, agent run counts,         and, Most-recent-first page of a plan's events, optionally filtered to one         ta, SqliteAgentEventReader

### Community 90 - "2026 06 12 Code Review Remediation M1 M5"
Cohesion: 0.23
Nodes (12): Code Review Remediation Plan (M1-M5), Embedded Coordinators as API Lifespan Threads, Per-agent Consumer Groups for task.assigned, Session Registry (202 + session_id endpoints), Single-writer Task State, Redis-to-SSE Bridge + SSEBroker Fan-out, Outbox Relay (poller to SSE + telemetry), Multi-turn Chat with Commit (MessageResponse) (+4 more)

### Community 91 - "Package"
Cohesion: 0.17
Nodes (11): dependencies, @anthropic-ai/sdk, dagre, @fontsource/ibm-plex-mono, @fontsource/ibm-plex-sans, immer, nanoid, react-dom (+3 more)

### Community 92 - "React Router Dom"
Cohesion: 0.18
Nodes (12): compareIndexes(), compilePath(), computeScore(), decodePath(), explodeOptionalSegments(), flattenRoutes(), matchPath(), matchRouteBranch() (+4 more)

### Community 93 - "Config"
Cohesion: 0.31
Nodes (10): add_request_id(), configure_logging(), _is_sensitive(), _mask_value(), Any, src/api/logging/config.py — structured logging + mandatory secret masking.  Sing, structlog processor: redact sensitive keys + SecretStr values., structlog processor: stamp every record with the current request_id. (+2 more)

### Community 94 - "Agent Repo"
Cohesion: 0.24
Nodes (4): AgentRepository, AgentSpec, Protocol, User-managed at runtime (full CRUD). delete() must be guarded: refuse if     the

### Community 95 - "Outbox"
Cohesion: 0.18
Nodes (5): Session, src/infra/db/outbox.py — SqliteOutbox (the Outbox port).  add() INSERTs on the U, SqliteOutbox, Session, sessionmaker

### Community 96 - "Plan Lifecycle"
Cohesion: 0.20
Nodes (11): Pause Gate (paused flag, auto-pause, resume = manual retry), Plan Lifecycle (the nine-phase machine), ARCHITECTURE No-LLM Passthrough, Conversational Phases (multi-turn with commit), Driver Model (who advances each phase), JIT Enrichment (one task-less goal per step), Nine-Phase Plan Machine, Append-Only Replan Loop (+3 more)

### Community 97 - "2026 06 13 Api Stability Frontend Recove"
Cohesion: 0.24
Nodes (11): API Stability + Frontend Recovery Backlog, Architecture / Phase-review Run Endpoints, gitignore lib/ Swallowing frontend/src/lib, Provider Tool-use Error Guard (PlannerRuntimeError classification), Architecture Phase Fix Plan (backend-first), GET /plan/architecture/status (reload-safe readiness), Auto-start Architecture on approve-brief, Gate Readiness Desync (Overview vs GatePanel vs LifecycleRail) (+3 more)

### Community 98 - "Readme"
Cohesion: 0.20
Nodes (10): ARCHITECTURE No-LLM Passthrough, Workspace Port (git branching = the rollback), GateHandler (unconditional PAUSED), PlanDispatcher (advance_plan phase router), PlanningHandler (ARCHITECTURE passthrough + ENRICHING JIT), Git Worktree Workspace Rules, Nine-Phase Plan Lifecycle (PlanPhase), AIPOM — Agent Orchestrator (root README) (+2 more)

### Community 99 - "Common"
Cohesion: 0.29
Nodes (9): ErrorDetail, ErrorEnvelope, ErrorResponse, HealthResponse, PlanConflictResponse, BaseModel, src/api/schemas/common.py — Shared primitive DTOs., Consistent error body for the control-plane endpoints.      Stack traces are nev (+1 more)

### Community 100 - "Capability Repo"
Cohesion: 0.27
Nodes (4): CapabilityRepository, Capability, Protocol, Capabilities have their own identity and will grow tooling relationships.     Us

### Community 101 - "Model Provider Repo"
Cohesion: 0.27
Nodes (4): ModelProviderRepository, ModelProvider, Protocol, User-managed at runtime. delete() CASCADES to the provider's models     (provide

### Community 102 - "Stub Reasoner"
Cohesion: 0.24
Nodes (7): _parse_goals(), Capability, ConversationMode, Goal, Plan, Task, src/infra/reasoner/stub_reasoner.py — deterministic Reasoner (no LLM).  Drives t

### Community 103 - "Package"
Cohesion: 0.20
Nodes (9): name, private, scripts, build, dev, generate:api, preview, type (+1 more)

### Community 104 - "@Tanstack React Query"
Cohesion: 0.24
Nodes (10): addConsumeAwareSignal(), ensureQueryFn(), fetch(), fetchNextPage(), fetchPreviousPage(), getNextPageParam(), hasNextPage(), infiniteQueryBehavior() (+2 more)

### Community 105 - "Readme"
Cohesion: 0.22
Nodes (9): Orchestrator CI Workflow, StubReasoner (deterministic grammar), The Truth Test (dual-backend verification), API Layer (thin routers), In-Memory Fakes (testing/fakes.py), Transactional Outbox Pattern, Dry-Run + Stub Default Mode, Planned CI Pipeline (per-PR vs nightly split) (+1 more)

### Community 106 - "Readme"
Cohesion: 0.31
Nodes (9): Version CAS (optimistic concurrency), Worker Loop (worker_tick / drive_plan), StaleVersionError (optimistic-lock conflict), create()/reconstruct() Factory Split, identity.new_id() (centralized id generation), Navigation Derived, Never Stored, Plan Lease (claim_one_unit / heartbeat / release), PlanRepository Port (+1 more)

### Community 107 - "Exceptions"
Cohesion: 0.25
Nodes (7): FastAPI, src/api/exceptions.py — the ONE error -> HTTP mapping layer (roadmap 4.1).  Rout, register_exception_handlers(), src/api/security.py — control-plane authentication.  Prototype-grade single shar, require_api_token(), Request lacked valid credentials (control-plane token)., UnauthorizedError

### Community 108 - "Tasks Errors"
Cohesion: 0.25
Nodes (3): GoalNotFoundError, TaskNotFoundError, test_edit_unknown_goal_raises()

### Community 109 - "Agent Factory"
Cohesion: 0.25
Nodes (6): AgentFactory, AgentSpec, Any, Capability, RetryPolicy, Factory for AgentSpec — same create/reconstruct split. Demonstrates the pattern

### Community 110 - "Base"
Cohesion: 0.31
Nodes (3): T, Repository ports (interfaces). Implementations live in infra and have a factory, Repository

### Community 111 - "Error Handler"
Cohesion: 0.25
Nodes (7): catch_domain_errors(), die(), err(), src/infra/cli/error_handler.py — centralised CLI error handling.  Policy:   - us, Route typed errors to stderr + exit(1); log the unexpected ones., F, NoReturn

### Community 112 - "2026 07 03 Working Prototype Reasoner Fr"
Cohesion: 0.31
Nodes (9): Working Prototype Plan - Real Reasoner + Frontend Re-point, ARCHITECTURE Phase as No-LLM Passthrough, JIT ENRICHING (per-goal task population), OpenAIReasoner, Providers Catalog Credential Resolution, StubReasoner (deterministic ask:/goal: grammar), Tool-calling Agent Loop (run_tool_session), Two-method Reasoner Port (converse + enrich_goal) (+1 more)

### Community 113 - "Pre Refactor Backend"
Cohesion: 0.39
Nodes (9): Pre-refactor Architecture Overview, Project Spec Governance (propose/diff/apply), Orchestration Authority Matrix, TaskGraphOrchestrator (goal orchestrator), Pre-refactor Roadmap Checklist, Legacy: pre-refactor backend features, Decision Gate & Decision History, GitHub PR Gate (+1 more)

### Community 114 - "Claude"
Cohesion: 0.25
Nodes (8): Application Layer (use cases + phase handlers), Cooperative Pausing (between units, never mid-run), Domain Layer (frozen pure core), CLAUDE.md — AIPOM Contribution Contract, Domain Freeze (Phase 0), Hexagonal / Clean Architecture Dependency Rule, Pause Gate (un-freeze #3), Attempt History on the Task

### Community 115 - "Agent Loop"
Cohesion: 0.32
Nodes (7): _accumulate_usage(), Any, BaseModel, The tool-calling agent loop — the old BasePlannerRuntime, ported.  run_tool_sess, Run the loop on ``messages`` (mutated in place: assistant turns and tool     res, run_tool_session(), SessionResult

### Community 116 - "Package"
Cohesion: 0.25
Nodes (8): devDependencies, @hey-api/openapi-ts, @types/dagre, @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react

### Community 117 - "React Router Dom"
Cohesion: 0.29
Nodes (7): cancel(), constructor(), emit(), onSettle(), resolveData(), subscribe(), trackPromise()

### Community 118 - "@Tanstack React Query"
Cohesion: 0.36
Nodes (8): ensureInfiniteQueryData(), ensureQueryData(), fetchQuery(), isStaleByTime(), prefetchQuery(), resolveStaleTime(), timeUntilStale(), usePrefetchQuery()

### Community 119 - "Integration Guide"
Cohesion: 0.29
Nodes (7): Chat Persistence (plan_chat_messages), ENRICHING Just-in-Time Goal Breakdown, INTEGRATION_GUIDE — Frozen Port Contracts, OpenAIReasoner (tool-calling agent loop), Reasoner Port (converse / enrich_goal), backend/docs Index, Conversation Use Cases (discovery/replanning turns)

### Community 120 - "Metrics"
Cohesion: 0.52
Nodes (6): AgentMetrics, LlmMetrics, metrics(), MetricsResponse, BaseModel, /api/metrics — the global (or per-plan) telemetry roll-up.  Aggregates the agent

### Community 121 - "Runner"
Cohesion: 0.52
Nodes (6): BaseModel, /api/runner — agent-runner configuration status.  `GET /runner/status` re-runs t, runner_status(), RunnerAgentStatus, RunnerBinaryStatus, RunnerStatusResponse

### Community 123 - "Control"
Cohesion: 0.29
Nodes (6): finish_review(), control — the human commands that drive the two gates.    AWAITING_REVIEW (pre-e, Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->     DISCOV, Human "finish" at the post-execution gate: REVIEW -> DONE. This is the ONLY, reopen_discovery(), PlanCompleted

### Community 124 - "Capability Matching"
Cohesion: 0.29
Nodes (5): AgentSpec, Bind unbound tasks to agents by capability. Returns task ids that fell         b, match_agent(), AgentSpec, Pure function: first agent whose capabilities cover the requirements.     Return

### Community 126 - "Readme"
Cohesion: 0.53
Nodes (6): Outbox Relay Thread, SSEBroker (per-client queue fan-out), Coarse Outbox Events (transactional), event_id Dedup (at-least-once to effectively-once), Fine Agent Events (best-effort telemetry), Two Event Streams, One Delivery Path

### Community 127 - "Capability Factory"
Cohesion: 0.40
Nodes (3): CapabilityFactory, Any, Capability

### Community 128 - "Lifecycle"
Cohesion: 0.40
Nodes (5): Enum, str, Lifecycle value objects shared by goals and tasks.  `Status` lives here (not in, Lifecycle state shared by goals and tasks. str-based so comparisons and JSON, Status

### Community 129 - "Clock"
Cohesion: 0.40
Nodes (3): datetime, Real Clock adapter — the only place the wall clock is read for domain logic.  Th, SystemClock

### Community 130 - "Taxonomy"
Cohesion: 0.33
Nodes (5): classify_failure(), FailureKind, src/infra/runtime/taxonomy.py — subprocess outcome -> the SHARED FAILURE TAXONOM, The taxonomy and RetryPolicy must agree on what is terminal., test_classifier_terminal_kinds_align_with_retry_policy()

### Community 131 - "Test Chat Repository"
Cohesion: 0.53
Nodes (5): _msg(), SqliteChatRepository: per-plan ordering, plan isolation, meta round-trip. The in, test_append_and_list_preserve_order(), test_meta_and_timestamp_round_trip(), test_plans_are_isolated()

### Community 132 - "2026 06 13 Architecture Session Hardenin"
Cohesion: 0.47
Nodes (6): Architecture Session Hardening Plan, Typed ArchitectureRoadmap Artifact, Auto-finalize on Budget Exhaustion, Cooperative cancel_check User Interrupt, Submit-tool Name Mismatch Bug, Flag-based Terminal-tool Detection

### Community 133 - "React Router Dom"
Cohesion: 0.53
Nodes (6): getFormSubmissionInfo(), isButtonElement(), isFormDataSubmitterSupported(), isFormElement(), isHtmlElement(), isInputElement()

### Community 134 - "Dependencies"
Cohesion: 0.50
Nodes (4): get_container(), get_uow(), src/api/dependencies.py — the API's dependency surface over AppContainer.  One p, set_container()

### Community 135 - "Engine"
Cohesion: 0.40
Nodes (4): Engine, db_url_for_home(), Path, Return the SQLite URL for the database under ``orchestrator_home``.

### Community 136 - "Main"
Cohesion: 0.40
Nodes (4): src/infra/worker/main.py — the worker entrypoint (the orchestration cadence).  W, Run the claim-and-drive loop until `stop` is set (or forever).      lease_second, run_worker_forever(), Event

### Community 137 - "Test Migrations"
Cohesion: 0.50
Nodes (4): _columns(), The fresh Alembic chain must produce the same schema the ORM metadata declares (, name -> nullable, for drift comparison., test_alembic_upgrade_head_matches_metadata()

### Community 139 - "Readme"
Cohesion: 0.50
Nodes (4): AgentRunner Port, backoff_for(attempt) exponential backoff, RetryPolicy (retry/backoff decision), Shared Failure Taxonomy (FailureKind)

### Community 140 - "Navigation"
Cohesion: 0.50
Nodes (3): _goal_ready(), datetime, Goal

### Community 141 - "Main"
Cohesion: 0.33
Nodes (4): plan(), Idempotent bootstrap data (capabilities, default agent, reasoner)., Read-only plan inspection., seed()

## Ambiguous Edges - Review These
- `Orchestrator CI Workflow` → `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: references
- `Orchestrator CI Workflow` → `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: conceptually_related_to

## Knowledge Gaps
- **109 isolated node(s):** `agent-orchestrator`, `type`, `name`, `private`, `version` (+104 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Orchestrator CI Workflow` and `Dry-Run + Stub Default Mode`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `Orchestrator CI Workflow` and `Do-Not-Do List (rejected improvements)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `react` connect `React Router Components Vendor` to `React Router Vendor Core`, `App Shell & Chat Panel`, `React Router Dom`, `React Router Dom`, `Chunk E55Nsntn`, `React Router Dom`, `Gatepanel`, `Package`, `React Router Dom`?**
  _High betweenness centrality (0.208) - this node is a cross-community bridge._
- **Why does `ConsoleDock()` connect `App Shell & Chat Panel` to `React Router Components Vendor`?**
  _High betweenness centrality (0.170) - this node is a cross-community bridge._
- **Why does `Task` connect `Plans API Router` to `Lifecycle`, `Replan Use Case`, `Plan Creation & Use-Case Tests`, `Domain Error Hierarchy`, `Agent Events & Task Results`, `Reference Data Repositories`, `Dummy Runner & Outbox Fakes`, `Plan Edit Service`, `Reasoner Config & CLI`, `Navigation Scan & Aggregate Tests`, `Pause/Resume Gate`, `Fake Clock & In-Memory Repos`, `Advance-Plan Dispatcher & Goal Entity`, `Execution Handler Running Loop`, `Test Conversation And Planning`, `Task`, `Test Transitions`, `Context`, `Test Runner Taxonomy`, `Support`, `Task`, `Conversation`, `Test Worker Loop`, `Engine`, `Test Edge Cases`, `Test Reasoner Backoff`, `Agent Errors`, `Test Openai Reasoner`, `Agent Port`, `Test Advance Plan`, `Chat Repository`, `Test Full Cycle Llm`, `Reasoner Port`, `Tasks Errors`, `Fakes`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Are the 46 inferred relationships involving `AppContainer` (e.g. with `ConfigValue` and `AgentMetrics`) actually correct?**
  _`AppContainer` has 46 INFERRED edges - model-reasoned connections that need verification._
- **Are the 80 inferred relationships involving `Plan` (e.g. with `PhaseHandler` and `Signal`) actually correct?**
  _`Plan` has 80 INFERRED edges - model-reasoned connections that need verification._