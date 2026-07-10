# Graph Report - agent-orchestrator  (2026-07-10)

## Corpus Check
- 311 files · ~433,027 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3697 nodes · 8224 edges · 355 communities (187 shown, 168 thin omitted)
- Extraction: 77% EXTRACTED · 23% INFERRED · 0% AMBIGUOUS · INFERRED: 1895 edges (avg confidence: 0.64)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ce3781ba`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

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
- Architecture overview
- toast.ts
- API Layer
- Events
- Git flow and releases
- Documentation
- graphify reference: query, path, explain
- Events & observability
- Playwright E2E Plan — Architecture Phase Workflow (deferred)
- export_openapi.py
- ADR: Concurrency model — the per-plan lease IS the unit of parallelism
- History — the paper trail
- register_exception_handlers
- `PlanRepository` — persistence **and** the concurrency primitives
- Tests
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- Errors
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- AGENTS.md
- replan_mid_running
- CHANGELOG.md
- extraction-spec.md
- ARCHITECTURE No-LLM Passthrough
- ENRICHING Just-in-Time Goal Breakdown
- INTEGRATION_GUIDE — Frozen Port Contracts
- OpenAIReasoner (tool-calling agent loop)
- Reasoner Port (converse / enrich_goal)
- StubReasoner (deterministic grammar)
- The Truth Test (dual-backend verification)
- Workspace Port (git branching = the rollback)
- backend/docs Index
- SSEBroker (per-client queue fan-out)
- Conversation Use Cases (discovery/replanning turns)
- Crash-Safety Choreography (ExecutionHandler rules)
- ExecutionHandler (RUNNING pull-scan loop)
- GateHandler (unconditional PAUSED)
- In-Memory Fakes (testing/fakes.py)
- PlanDispatcher (advance_plan phase router)
- PlanningHandler (ARCHITECTURE passthrough + ENRICHING JIT)
- Worker Loop (worker_tick / drive_plan)
- Cooperative Pausing (between units, never mid-run)
- Plan Aggregate Root
- Append-Only Replan Loop
- Delete-Guard Integrity Rule (cascade down, guard up)
- DomainError base (stable codes + log-safe context)
- InvalidTransitionError
- StaleVersionError (optimistic-lock conflict)
- Coarse Outbox Events (transactional)
- event_id Dedup (at-least-once to effectively-once)
- Fine Agent Events (best-effort telemetry)
- create()/reconstruct() Factory Split
- identity.new_id() (centralized id generation)
- backoff_for(attempt) exponential backoff
- RetryPolicy (retry/backoff decision)
- Navigation Derived, Never Stored
- Plan Lease (claim_one_unit / heartbeat / release)
- PlanRepository Port
- Edit Service (structural edit rules)
- find_goal / find_task (shared lookups)
- match_agent (pure capability matcher)
- next_action(goals, now) — the navigation scan
- Status enum + TERMINAL set
- TaskResult (typed output + idempotency record)
- Dual-Backend Truth Test
- CLAUDE.md — AIPOM Contribution Contract
- Domain Freeze (Phase 0)
- Shared Failure Taxonomy (FailureKind)
- Hexagonal / Clean Architecture Dependency Rule
- Nine-Phase Plan Lifecycle (PlanPhase)
- Pause Gate (un-freeze #3)
- Transactional Outbox Pattern
- Two Event Streams, One Delivery Path
- Envelope-Encrypted Secrets
- Plan-as-Document (one JSON document in plans.data)
- Re-enterable SqliteUnitOfWork
- Version CAS (optimistic concurrency)
- agent_events Telemetry Stream
- Outbox Relay → SSE Delivery
- Transactional Outbox
- Shared Failure Taxonomy (FailureKind)
- Git-Branching Workspace as Rollback
- Pull-Scan Navigation (next_action)
- Two-Transaction Choreography
- Worker Claim/Drive/Release Loop
- SSE Bridge + Invalidate-Not-Patch Data Flow
- Known Issue H1: Double-Execution Window (lease < task timeout)
- Known Issue H2: Poisoned-Plan Starvation
- Composition Root (AppContainer, env read once)
- Hexagonal Dependency Rule
- ARCHITECTURE No-LLM Passthrough
- Conversational Phases (multi-turn with commit)
- Driver Model (who advances each phase)
- JIT Enrichment (one task-less goal per step)
- Nine-Phase Plan Machine
- Append-Only Replan Loop
- Per-Plan Lease (sequential per plan)
- Domain Freeze (Phase-0, 2026-07-02)
- Planning Backoff Gate (un-freeze #2)
- Container-in-App Boundary Violation (old backend)
- Rate-Limit Terminal Failure (2026-07-09 live run)
- Embedded Coordinators as API Lifespan Threads
- Per-agent Consumer Groups for task.assigned
- Session Registry (202 + session_id endpoints)
- Single-writer Task State
- Redis-to-SSE Bridge + SSEBroker Fan-out
- Architecture / Phase-review Run Endpoints
- gitignore lib/ Swallowing frontend/src/lib
- Provider Tool-use Error Guard (PlannerRuntimeError classification)
- Auto-finalize on Budget Exhaustion
- Cooperative cancel_check User Interrupt
- Submit-tool Name Mismatch Bug
- Flag-based Terminal-tool Detection
- GET /plan/architecture/status (reload-safe readiness)
- Auto-start Architecture on approve-brief
- Gate Readiness Desync (Overview vs GatePanel vs LifecycleRail)
- Playwright architecture-flow E2E Spec
- Dry-run SSE Delivery Gap
- Abandoned-iteration Rule / Tolerant Finalize
- Append-only Replan Loop with Iteration Counter
- Domain Freeze after Phase 0
- Driver Model / Worker Claim Predicate
- Integration Truth Test (real-SQLite re-run)
- Nine-phase Plan Machine
- Outbox Relay (poller to SSE + telemetry)
- Per-plan Lease
- Shared Failure Taxonomy
- Worker Tick Reports Progress, Not Claiming
- JIT ENRICHING (per-goal task population)
- Multi-turn Chat with Commit (MessageResponse)
- OpenAIReasoner
- Providers Catalog Credential Resolution
- StubReasoner (deterministic ask:/goal: grammar)
- Tool-calling Agent Loop (run_tool_session)
- Two-method Reasoner Port (converse + enrich_goal)
- Bounded Task Attempt History
- Double-execution Window (H1)
- Poisoned-plan Starvation (H2)
- Three Futures (Surgeon / Architect / Heretic)
- Old Plan Lifecycle (discovery -> architecture -> phase_active -> phase_review -> done)
- Reconciler (expired leases, stuck tasks)
- TaskGraphOrchestrator (goal orchestrator)
- Decision Gate & Decision History
- GitHub PR Gate
- Planner Sessions (202/session/poll machinery)
- Redis Event Topology
- SSE Invalidate-don't-patch Pattern
- Attempt History on the Task
- Goal-Level Parallelism (lease granularity)
- H1: Double-Execution Window Defect
- H2: Poisoned-Plan Starvation Defect

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
- `Orchestrator CI Workflow` --references--> `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml → README.md
- `Orchestrator CI Workflow` --conceptually_related_to--> `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml → ROADMAP.md
- `test_start_clears_retry_gate()` --calls--> `Task`  [INFERRED]
  backend/tests/unit/orchestration/test_backoff_gate.py → backend/src/domain/entities/task.py
- `container()` --calls--> `AppContainer`  [INFERRED]
  backend/tests/integration/test_agent_runner_factory.py → backend/src/infra/container.py

## Import Cycles
- None detected.

## Communities (355 total, 168 thin omitted)

### Community 0 - "Generated API Types"
Cohesion: 0.02
Nodes (235): AgentEventResponse, AgentMetrics, ClientOptions, ConfigDeleteValueData, ConfigDeleteValueError, ConfigDeleteValueErrors, ConfigDeleteValueResponse, ConfigDeleteValueResponses (+227 more)

### Community 1 - "Config & Reference API"
Cohesion: 0.05
Nodes (62): get_container(), get_uow(), src/api/dependencies.py — the API's dependency surface over AppContainer.  One p, set_container(), ConfigValue, delete_value(), get_scope(), BaseModel (+54 more)

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
Nodes (42): GoalGroupNode(), KIND_COLOR, PhaseTimeline(), WALK, KIND_COLOR, nodeTypes, NOTE: no `= []` defaults here — a fresh array per render would change, metaFor() (+34 more)

### Community 6 - "Immer/Zustand Vendor"
Cohesion: 0.12
Nodes (52): applyPatches(), constructor(), createDraft(), createProxy(), createProxyProxy(), createScope(), current(), currentImpl() (+44 more)

### Community 7 - "App Shell & Chat Panel"
Cohesion: 0.11
Nodes (31): App(), PlanShell(), StaleNotice(), ChatPanel(), MODE_HINTS, ConsoleDock(), lineColor(), DetailPanel() (+23 more)

### Community 8 - "Outbox Relay & SSE Events"
Cohesion: 0.06
Nodes (35): AbstractEventLoop, Session, sessionmaker, src/api/outbox_relay.py — delivers outbox rows to their consumers (roadmap 4.4)., One relay pass. Returns (rows delivered, new agent-events cursor)., Thread body: poll until told to stop. Own connections throughout —     never tou, relay_once(), run_outbox_relay() (+27 more)

### Community 9 - "TanStack Query Vendor Core"
Cohesion: 0.05
Nodes (22): fetchInfiniteQuery(), getCurrentQuery(), getPreviousPageParam(), getQueries(), getResult(), hashKey(), hasObjectPrototype(), hasPreviousPage() (+14 more)

### Community 10 - "Replan Use Case"
Cohesion: 0.06
Nodes (40): Plan, BaseModel, datetime, FailureKind, Goal, NextAction, Task, TaskResult (+32 more)

### Community 11 - "Plans API Router"
Cohesion: 0.16
Nodes (45): agent_events(), AgentEventResponse, chat_history(), ChatMessageResponse, create(), CreatePlanRequest, discovery(), edit_plan() (+37 more)

### Community 12 - "Plan Creation & Use-Case Tests"
Cohesion: 0.14
Nodes (42): catalogs(), edit(), make_agent(), _paused_running_plan(), _plan_with_goal(), Tests for create_plan / apply_edit / control use cases., Locked rebind-on-edit rule: editing required_capabilities RE-RUNS     match_agen, Capability ids are validated at the edit boundary (DESIGN_NOTES #5): a bad     i (+34 more)

### Community 13 - "Domain Error Hierarchy"
Cohesion: 0.07
Nodes (35): Catalog-resolved: config key agent_runner.mode selects dry-run         (default,, ClaudeCodeRunner, PiAgentRunner, Runs `pi --model <m> -p "<prompt>"` in the workspace (pi-mono CLI)., Runs `claude --dangerously-skip-permissions -p "<prompt>"`., build_agent_runner(), CatalogAgentRunner, _invalid() (+27 more)

### Community 14 - "Agent Events & Task Results"
Cohesion: 0.08
Nodes (21): ABC, Task, TaskResult, AgentEvent, BaseModel, Fine-grained agent runtime events — tool calls, steps, tokens streamed by the pi, build_task_prompt(), CliAgentRunner (+13 more)

### Community 15 - "Encrypted Secret Store"
Cohesion: 0.10
Nodes (21): BaseModel, SecretRef — a reference (URI) to a secret in the secret store.  Infra-local on p, Canonical ref for a provider's API key., SecretRef, Session, sessionmaker, src/infra/db/secret_store.py — the SQLite secret store (envelope encryption).  E, Internal infra helper: the single place plaintext is unwrapped.          Used by (+13 more)

### Community 16 - "Git Workspace Port"
Cohesion: 0.11
Nodes (18): _git(), _git_ok(), GitBranchWorkspace, GitWorkspaceHandle, LocalDirWorkspace, Path, src/infra/git/workspace.py — Workspace adapters (the async Workspace port).  Git, The rollback: nothing the agent did reaches the plan branch. (+10 more)

### Community 17 - "Provider & Model Catalog"
Cohesion: 0.11
Nodes (13): IAModel, BaseModel, ModelProvider, BaseModel, ModelFactory, ProviderFactory, Any, ModelProvider (+5 more)

### Community 18 - "Reference Data Repositories"
Cohesion: 0.08
Nodes (28): CapabilityNotFoundError, EntityAlreadyExistsError, Create rejected because an entity with this id already exists., Delete-guard: refuse to delete reference data still in use by something active., ReferencedEntityInUseError, One UoW per worker/request — the instance is not thread-safe., _capability_from_row(), AgentSpec (+20 more)

### Community 19 - "Dummy Runner & Outbox Fakes"
Cohesion: 0.15
Nodes (27): DummyBehavior, How the dummy should behave for a given task id., Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING., review_replan(), request_replan — enter the conversational re-plan (state machinery only).  Two e, request_replan(), agent(), goal() (+19 more)

### Community 20 - "React Router Components Vendor"
Cohesion: 0.09
Nodes (36): react, Await(), convertRouteMatchToUiMatch(), createMemoryHistory(), createMemoryRouter(), createRoutesFromChildren(), DataRoutes(), _extends2() (+28 more)

### Community 21 - "LLM Client Runtime"
Cohesion: 0.11
Nodes (22): AssistantTurn, LLMClient, OpenAIChatClient, Any, BaseModel, Protocol, The OpenAI-compatible chat client (async) — the old adapter's request layer.  LL, Normalize the provider's token usage into prompt/completion/total.         Retur (+14 more)

### Community 22 - "Plan Edit Service"
Cohesion: 0.13
Nodes (32): Any, _require(), InvalidEditError, GoalAlreadyRunningError, Edit/mutation rejected because the goal is already running or finished., add_task(), _assert_acyclic(), _assert_editable() (+24 more)

### Community 23 - "Reasoner Config & CLI"
Cohesion: 0.09
Nodes (31): BaseModel, /api/reasoner — reasoner configuration status.  `GET /reasoner/status` re-runs t, reasoner_status(), ReasonerStatusResponse, cli(), AIPOM agent orchestrator., Catalog-resolved: config key reasoner.mode selects stub (default,         no sec, load_master_key() (+23 more)

### Community 24 - "Navigation Scan & Aggregate Tests"
Cohesion: 0.19
Nodes (30): exec_plan(), goal(), Aggregate orchestration, navigation, edits, binding, factories — the behaviors t, Un-freeze #3: a terminal task failure pauses the plan (goal stays open,     phas, task(), test_advance_to_next_goal(), test_all_done_returns_none(), test_dependent_goal_selectable_after_dependency_done() (+22 more)

### Community 25 - "Pause/Resume Gate"
Cohesion: 0.13
Nodes (28): Clear the pause gate and requeue failed work (the manual retry): FAILED     task, resume(), pause_plan(), pause/resume — the human pause gate and the manual retry (un-freeze #3).  Pause, Human pause command. Idempotent: pausing an already-paused plan is a     no-op (, Human resume command = the manual retry. Raises InvalidTransitionError     (422), resume_plan(), drive() (+20 more)

### Community 26 - "Fake Clock & In-Memory Repos"
Cohesion: 0.08
Nodes (21): _Claim, _Handle, InMemoryCapabilityRepository, InMemoryOutbox, InMemoryPlanRepository, InMemoryUnitOfWork, NoOpWorkspace, Capability (+13 more)

### Community 27 - "Advance-Plan Dispatcher & Goal Entity"
Cohesion: 0.18
Nodes (7): Goal, BaseModel, Status, Phase-level chunk owning an ordered task list. Guarded self-transitions,     cal, Close the goal as SKIPPED (its iteration was abandoned by a replan).         All, Re-enter a finished goal because one of its tasks was reopened (human         re, test_goal_lifecycle()

### Community 28 - "Execution Handler Running Loop"
Cohesion: 0.10
Nodes (30): ExecutionHandler, Goal, Plan, Task, TaskResult, ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.  This is the, Scan exhausted: RUNNING -> REVIEW (post-exec gate), then pause for the         h, Goal-failure policy (amended by un-freeze #3): a goal whose remaining         wo (+22 more)

### Community 29 - "TanStack QueryClient Internals"
Cohesion: 0.12
Nodes (31): add(), build(), defaultQueryOptions(), difference(), fetchOptimistic(), functionalUpdate(), get(), getCurrentResult() (+23 more)

### Community 30 - "Planning Handler"
Cohesion: 0.13
Nodes (21): Enum, str, Phase handlers: one concern per phase.  advance_plan is a thin DISPATCHER that r, What one advance step tells the worker loop to do next., Signal, _next_unenriched(), PlanningHandler, Goal (+13 more)

### Community 31 - "Reference Repos"
Cohesion: 0.10
Nodes (11): Session, sessionmaker, Repo-level default-agent marker (not part of AgentSpec)., _is_locked(), Session, sessionmaker, _T, src/infra/db/_session.py — shared transactional runner with lock-retry.  Single (+3 more)

### Community 32 - "Test Conversation And Planning"
Cohesion: 0.18
Nodes (27): One converse() turn. goals=None means "still conversing" (the message is     a q, ReasonerReply, _drive_planning(), goal(), handler(), plan_in(), Plan, PlanPhase (+19 more)

### Community 33 - "Test Transitions"
Cohesion: 0.16
Nodes (23): Arm the pause gate: the claim predicate skips a paused plan, so the         work, InvalidTransitionError, A state transition was attempted that the current status does not allow., mk_goal(), mk_task(), Exhaustive state-transition tests — the guarantee against transition bugs., test_goal_illegal_transition_raises(), test_goal_reopen_done_to_running() (+15 more)

### Community 34 - "Readme"
Cohesion: 0.13
Nodes (5): backend/docs, Conventions, Decisions, Guardrails, Orchestration Authority Matrix

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
Cohesion: 0.20
Nodes (16): Raised by an AgentRunner when a task run fails. Carries a human-readable     `re, TaskFailed, CollectingEventSink, make_cli(), Path, The CLI runner against a scripted fake CLI: success path, and every FailureKind, Runs an arbitrary executable — the test controls the CLI's behavior., run() (+8 more)

### Community 39 - "@Tanstack React Query"
Cohesion: 0.13
Nodes (25): bindMethods(), canFetch(), canRun(), constructor(), continue(), createRetryer(), execute(), fetchState() (+17 more)

### Community 40 - "Support"
Cohesion: 0.05
Nodes (40): 0.1 Finish remaining domain work, 0.2 Concurrency ADR (required even though sequential now), 0.3 Settle the last domain decisions (before freeze), 1.1 Feature triage (AI-assisted, against the concrete current workflow), 1.2 Conflict list (old vs new) + resolution, 1.3 Preservation list → port map, 1.4 Data migration — CLEAN BREAK (decided), 1.5 Integration rollback — USER-HANDLED (decided) (+32 more)

### Community 41 - "Reference Repos"
Cohesion: 0.24
Nodes (6): _guard_model_in_use(), Session, sessionmaker, Guard-up: a model referenced by config (the model_role tier mapping)     or boun, Prototype-grade reference scan over non-terminal plan JSON documents., _referenced_by_active_plan()

### Community 42 - "Activity"
Cohesion: 0.16
Nodes (17): ConnectionIndicator(), ThemeToggle(), queryClient, useMetrics(), applyTheme(), currentTheme(), getInitialTheme(), Theme (+9 more)

### Community 43 - "Task"
Cohesion: 0.09
Nodes (11): datetime, FailureKind, Status, TaskResult, True if the task is eligible to run at `now` — i.e. not gated by an         unex, Return to PENDING for retry. Result cleared; attempts preserved.         `not_be, Mark the task terminal-SKIPPED without running it (work became         unnecessa, Terminal-skip an in-flight task whose iteration was abandoned by a         repla (+3 more)

### Community 44 - "Conversation"
Cohesion: 0.07
Nodes (30): ChatStore, Per-plan conversation history (DISCOVERY / REPLANNING). Writes run     OUTSIDE t, _conversation_turn(), ConversationResult, discovery_message(), BaseModel, PlanPhase, conversation — the chat-driven phases (the driver model's third driver).  DISCOV (+22 more)

### Community 45 - "Planner Orchestrator"
Cohesion: 0.06
Nodes (33): 1. Discovery — `plan init`, 1. Install dependencies, 2. Architecture — `plan architect`, 2. Initialize local config, 3. Phase review — `plan review`, 3. Register at least one agent, 4. Inspecting the plan — `plan status` / `plan logs`, 4. Start the plan workflow (+25 more)

### Community 46 - "Test Api"
Cohesion: 0.10
Nodes (8): _plan_at_awaiting_review(), The thin API over TestClient: the plan lifecycle through HTTP, the error-> HTTP, Write agent_events rows directly through the sink for read-side tests., Drive a stub plan discovery->architecture->enriching->awaiting_review by     com, _seed_agent_events(), test_agent_events_read_endpoint(), test_metrics_endpoint(), test_pause_resume_and_edit_over_http()

### Community 47 - "React Router Dom"
Cohesion: 0.13
Nodes (22): callDataStrategyImpl(), callLoaderOrAction(), convertRoutesToDataRoutes(), createBrowserHistory(), createBrowserRouter(), createHashHistory(), createHashRouter(), createRouter() (+14 more)

### Community 48 - "Test Worker Loop"
Cohesion: 0.15
Nodes (16): plan_with_chain(), Worker-loop tests: the full claim->drive->release cycle, crash recovery via leas, Only ARCHITECTURE / ENRICHING / RUNNING are worker-claimable. Conversational, Regression for the hot claim->release spin: a claimable plan whose only     work, Two goals where g2 depends on g1 — the classic case that produced     pending-no, Execution exhausts into REVIEW (the post-exec gate) — DONE only comes from     t, The reconciler-killer at the loop level: g2 never executes before g1 is     done, Worker w1 CLAIMS the plan, completes one task, then 'dies' without     releasing (+8 more)

### Community 49 - "Tsconfig"
Cohesion: 0.10
Nodes (20): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleResolution (+12 more)

### Community 50 - "React Router Dom"
Cohesion: 0.18
Nodes (21): createPath(), createSearchParams(), getInvalidPathError(), getPathContributingMatches(), getResolveToMatches(), getSearchParamsForLocation(), getTargetMatch(), hasNakedIndexQuery() (+13 more)

### Community 51 - "Engine"
Cohesion: 0.07
Nodes (35): DummyAgentRunner, FakeClock, InMemoryChatStore, Mirrors SqliteChatRepository: per-plan append-only history, insertion     order, Implements AgentRunner with no LLM/subprocess. Scriptable per task id so     tes, A clock the test drives. advance() moves time forward so backoff gates and     l, _apply_pragmas(), build_engine() (+27 more)

### Community 52 - "Test Full Cycle"
Cohesion: 0.17
Nodes (18): Human approval at the pre-execution gate: advance into execution., resume_from_review(), create_plan(), RetryPolicy, create_plan — entry point that turns a brief into a persisted Plan.  Idempotent, Create a plan from a brief. Returns the plan id. Idempotent on request_id., THE FULL CYCLE on the real stack (SQLite UoW + stub reasoner + dummy runner):, Tick until the worker finds nothing to progress (gates/conversational         ph (+10 more)

### Community 53 - "Planning Errors"
Cohesion: 0.05
Nodes (22): EmptyPlanError, PlanAlreadyTerminalError, Operation rejected because the plan is already DONE or FAILED., A plan must have a brief / cannot be created empty., PlanFactory, Any, Plan, RetryPolicy (+14 more)

### Community 54 - "Test Edge Cases"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 55 - "Plan Repository"
Cohesion: 0.08
Nodes (23): 1.1 Per-agent consumer groups for `task.assigned` (Critical, S), 1.2 PEL recovery (High, S), 1.3 Dead code + packaging (Medium, S) — clears 10 of 14 failing tests, 1.4 Green tests + ruff (Medium), 1.5 Hotfix: discovery 409 leak (5 lines), 2.0 Spec staleness first (Medium, S), 2.1 Make the loops embeddable (stop semantics), 2.2 Extract runner functions (+15 more)

### Community 56 - "Openai Reasoner"
Cohesion: 0.15
Nodes (10): _build_task(), Any, Capability, ConversationMode, Goal, Plan, Task, src/infra/reasoner/openai_reasoner.py — the real Reasoner (OpenAI-compatible). (+2 more)

### Community 57 - "Gatepanel"
Cohesion: 0.14
Nodes (18): PostExecutionGate(), PreExecutionGate(), LifecycleRail(), StepState, WALK, useApprovePlan(), useCreatePlan(), useFinishReview() (+10 more)

### Community 58 - "@Tanstack React Query"
Cohesion: 0.14
Nodes (19): defaultMutationOptions(), find(), findAll(), getMutationDefaults(), getObserversCount(), getQueriesData(), invalidate(), invalidateQueries() (+11 more)

### Community 59 - "Gate Handler"
Cohesion: 0.11
Nodes (18): PhaseHandler, Plan, Protocol, Handles one advance step for the phase(s) it owns. Given the plan_id and the, GateHandler, Plan, GateHandler — owns the human-gate phases (AWAITING_REVIEW, REVIEW).  A gate paus, Outbox (+10 more)

### Community 60 - "Test Reasoner Backoff"
Cohesion: 0.21
Nodes (14): enriching_plan(), FailingReasoner, goal(), handler(), Plan, Task, Reasoner-failure handling in the worker-driven planning phases (un-freeze #2): a, The gate is durable: an armed plan is not claimed until the clock passes it — (+6 more)

### Community 62 - "Server"
Cohesion: 0.19
Nodes (12): APIRoute, _cors_origins(), create_app(), FastAPI, src/api/server.py — FastAPI application factory (the thin API).  Responsibilitie, Frontend origins allowed to read the API (incl. the SSE stream).     Defaults co, Clean operation IDs (`plans-create`) for typed client generators., Build the configured FastAPI application. Pass `container` explicitly in     tes (+4 more)

### Community 63 - "Readme"
Cohesion: 0.11
Nodes (14): `AgentSpec`, `base.py`, Entities, `Goal`, `Task`, Factories, On thin factories (e.g. `CapabilityFactory`), `backoff_for(attempt)` — the `retry_index - 1` explained (+6 more)

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
Cohesion: 0.09
Nodes (21): drive_plan(), run_worker — the orchestrator loop (Option A: loop-driven, pull-based).  This is, Advance one plan until it stops making progress. Returns (terminal signal,     u, One claim-and-drive cycle. Returns True only if actual work ADVANCED —     not m, worker_tick(), AgentRunner, AgentSpec, Protocol (+13 more)

### Community 69 - "Test Advance Plan"
Cohesion: 0.27
Nodes (16): env_factory(), The truth-test parametrization: every env-based orchestration test runs against, make_plan(), End-to-end orchestration tests for advance_plan. Runs against the in-memory doub, Simulate a crash after the agent ran but before txn2: the task is RUNNING     wi, Drive the plan through RUNNING. Execution exhausts into the REVIEW gate     (pau, run_to_completion(), test_agent_events_streamed_and_tagged() (+8 more)

### Community 70 - "@Tanstack React Query"
Cohesion: 0.20
Nodes (15): addObserver(), clear(), clearGcTimeout(), clearTimeout(), destroy(), isValidTimeout(), notify(), onSubscribe() (+7 more)

### Community 71 - "@Tanstack React Query"
Cohesion: 0.24
Nodes (15): createResult(), hasListeners(), isStale(), onQueryUpdate(), resolveQueryBoolean(), setOptions(), shallowEqualObjects(), shouldFetchOn() (+7 more)

### Community 72 - "Main"
Cohesion: 0.06
Nodes (34): catch_domain_errors(), die(), err(), ok(), src/infra/cli/error_handler.py — centralised CLI error handling.  Policy:   - us, Route typed errors to stderr + exit(1); log the unexpected ones., config(), config_get() (+26 more)

### Community 73 - "Tables"
Cohesion: 0.09
Nodes (31): ModelNotFoundError, ModelProviderNotFoundError, _model_from_row(), ModelProvider, ProjectDefinition, src/infra/db/reference_repos.py — SQLite reference-data repositories.  Implement, The entity owns its models: make the model rows match provider.models., SqliteConfigStore (+23 more)

### Community 74 - "Execution Model"
Cohesion: 0.18
Nodes (10): Engine policy, Referential integrity — two mechanisms, Schema at a glance, Secrets — envelope encryption, State directory, The data model, The lease (rows as ownership), The plan-as-document decision (+2 more)

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
Cohesion: 0.18
Nodes (10): get_request_id(), Request, src/api/middleware/request_logging.py — correlation id + request lifecycle logs., Return the current request's correlation id, or '-' outside a request., Bind a correlation id on the current context (e.g. background work)., _request_logging_enabled(), RequestLoggingMiddleware, set_request_id() (+2 more)

### Community 79 - "Ports"
Cohesion: 0.09
Nodes (22): 5. Invalid semantic version request, 6. Events endpoint timeout, After, Architecture Improvement, Before, Conclusion, Conclusion, Files Changed (+14 more)

### Community 80 - "Chat Repository"
Cohesion: 0.11
Nodes (18): 🔄 Autonomous Continuous Loop, 🔧 Foundation and Orchestration Core, 🔀 GitHub PR Integration, 🎯 Goal-Driven Execution, ✅ Implemented / Substantially Present, 🌐 Long-Term, 🏗️ Mid-Term, 🚀 Near-Term (+10 more)

### Community 81 - "Planner Repo"
Cohesion: 0.12
Nodes (8): Exception, FailureKind, Raised by a Reasoner when it cannot produce a usable turn/artifact — the     pla, ReasonerUnavailable, PlanRepository, Plan, Protocol, Single source of truth for plan persistence + the concurrency primitives.      T

### Community 82 - "Test Full Cycle Llm"
Cohesion: 0.23
Nodes (7): PlanPhase, Enum, str, The nine-phase machine (see MASTER_ROADMAP_FINAL.md):      DISCOVERY    — the fi, LLMStack, THE FULL CYCLE driven by the REAL reasoner implementation (OpenAIReasoner) on a, test_full_cycle_on_the_real_reasoner_with_scripted_llm()

### Community 83 - "Test Llm Client"
Cohesion: 0.33
Nodes (13): api_error(), assistant_message(), make_client(), OpenAIChatClient request behavior: transient retry with backoff, permanent fail-, An OpenAIChatClient whose chat.completions.create pops `responses`     (an Excep, response_with(), test_empty_choices_is_transient_and_retried(), test_malformed_tool_arguments_parse_to_empty_dict() (+5 more)

### Community 85 - "React Router Dom"
Cohesion: 0.22
Nodes (13): BrowserRouter(), flushSyncSafe(), generatePath(), getFormEncType(), HashRouter(), HistoryRouter(), logV6DeprecationWarnings(), MemoryRouter() (+5 more)

### Community 86 - "React Router Dom"
Cohesion: 0.19
Nodes (13): convertFormDataToSearchParams(), convertSearchParamsToFormData(), createClientSideRequest(), createKey(), createLocation(), isMutationMethod(), isSubmissionNavigation(), isValidMethod() (+5 more)

### Community 87 - "Execution Handler"
Cohesion: 0.12
Nodes (17): 1. Spec API using wrong model attribute, 2. Incorrect use case invocation, 3. Discovery endpoints allowed concurrent sessions, 4. Architectural boundary violation, Fix, Fix, Fix, Fix (+9 more)

### Community 88 - "Reasoner Port"
Cohesion: 0.12
Nodes (17): 1. Task lifecycle, 2. Goal lifecycle, 3. Planning lifecycle, 4. Spec governance, Application layer — `src/app`, Architecture notes on the current state, Architecture overview, Composition root — `src/infra/factory.py` (+9 more)

### Community 89 - "Agent Event Reader"
Cohesion: 0.18
Nodes (7): Any, Session, sessionmaker, src/infra/db/agent_event_reader.py — the read side of the agent_events stream., Global (or per-plan) roll-up: LLM sessions/tokens, agent run counts,         and, Most-recent-first page of a plan's events, optionally filtered to one         ta, SqliteAgentEventReader

### Community 90 - "2026 06 12 Code Review Remediation M1 M5"
Cohesion: 0.17
Nodes (10): Conventions, Data flow — one source of truth per kind of state, Screens and shell, The frontend — React dashboard, Type generation, AIPOM Dashboard HTML Entry Point, Frontend — the AIPOM dashboard, Rules that keep it coherent (+2 more)

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
Cohesion: 0.12
Nodes (16): Adversarial self-review, Control flow (as actually implemented), Do-not-do list, Load-bearing hacks (each verified), One task's life story, Part 0 — Archaeology, Part 1 — Stress tests (against current code), Part 2 — Three futures (+8 more)

### Community 97 - "2026 06 13 Api Stability Frontend Recove"
Cohesion: 0.15
Nodes (12): A1. Architecture & phase-review run endpoints  *(fixes the 409 dead-end)*, A2. Provider tool-use error guard, A3. Agents CRUD (edit + delete), B1. Fix `.gitignore` (the actual root cause), B2. Reconstruct the missing data layer so the frontend builds & runs, Backlog: API stability + frontend recovery, Context, Out of scope (follow-up) (+4 more)

### Community 98 - "Readme"
Cohesion: 0.15
Nodes (12): 🏗️ Architectural Invariants (Backend), Backend (Python) — all commands run from `backend/`, 🚀 Build & Run Commands, CLAUDE.md - AIPOM / Agent Orchestrator, 🧹 Code Style & Types, 🔌 Frontend <-> Backend Communication, Frontend (TypeScript / React / Vite), 🔄 Git Workspace Rules (+4 more)

### Community 99 - "Common"
Cohesion: 0.25
Nodes (10): _envelope(), ErrorDetail, ErrorEnvelope, ErrorResponse, HealthResponse, PlanConflictResponse, BaseModel, src/api/schemas/common.py — Shared primitive DTOs. (+2 more)

### Community 100 - "Capability Repo"
Cohesion: 0.27
Nodes (4): CapabilityRepository, Capability, Protocol, Capabilities have their own identity and will grow tooling relationships.     Us

### Community 101 - "Model Provider Repo"
Cohesion: 0.27
Nodes (4): ModelProviderRepository, ModelProvider, Protocol, User-managed at runtime. delete() CASCADES to the provider's models     (provide

### Community 102 - "Stub Reasoner"
Cohesion: 0.19
Nodes (9): new_id(), Single source of identity generation across factories, so the strategy     (uuid, _parse_goals(), Capability, ConversationMode, Goal, Plan, Task (+1 more)

### Community 103 - "Package"
Cohesion: 0.20
Nodes (9): name, private, scripts, build, dev, generate:api, preview, type (+1 more)

### Community 104 - "@Tanstack React Query"
Cohesion: 0.24
Nodes (10): addConsumeAwareSignal(), ensureQueryFn(), fetch(), fetchNextPage(), fetchPreviousPage(), getNextPageParam(), hasNextPage(), infiniteQueryBehavior() (+2 more)

### Community 105 - "Readme"
Cohesion: 0.50
Nodes (4): Orchestrator CI Workflow, Dry-Run + Stub Default Mode, Planned CI Pipeline (per-PR vs nightly split), Do-Not-Do List (rejected improvements)

### Community 107 - "Exceptions"
Cohesion: 0.14
Nodes (11): src/api/security.py — control-plane authentication.  Prototype-grade single shar, require_api_token(), BaseAppException, DomainError, Any, Exception, Base class for all domain-rule violations., Common root for every typed application error.      Subclasses set a class-level (+3 more)

### Community 108 - "Tasks Errors"
Cohesion: 0.25
Nodes (3): GoalNotFoundError, TaskNotFoundError, test_edit_unknown_goal_raises()

### Community 109 - "Agent Factory"
Cohesion: 0.06
Nodes (27): AgentSpec, Bind unbound tasks to agents by capability. Returns task ids that fell         b, AgentSpec, BaseModel, Definition of an agent: who it is, what it can do, how it retries,     and which, Capability, BaseModel, A named capability an agent can satisfy, bundling the tools it implies. (+19 more)

### Community 110 - "Base"
Cohesion: 0.31
Nodes (3): T, Repository ports (interfaces). Implementations live in infra and have a factory, Repository

### Community 111 - "Error Handler"
Cohesion: 0.15
Nodes (13): Context, Key risks, Stage 1 — Restore `src/domain/ports/` (structural, zero behavior), Stage 2 — Chat persistence substrate, Stage 3 — Conversational port + conversation rework + ARCHITECTURE passthrough + chat API, Stage 4 — JIT ENRICHING: per-goal task population, checkpointed, Stage 5 — LLM runtime infra (the old architecture, ported), Stage 6 — OpenAIReasoner + catalog resolution + seed CLI (+5 more)

### Community 114 - "Claude"
Cohesion: 0.09
Nodes (20): Advancing and pausing, Aggregates, `Plan` — the aggregate root, The nine-phase machine, The replan loop (append-only), Why task transitions live on `Plan`, not on `Goal`, 1. `next_action(goals, now)` — the scan (services/navigation.py), 2. The aggregate owns transitions (aggregates/planner_orchestrator.py) (+12 more)

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
Cohesion: 0.15
Nodes (13): AgentRunner, API → use-case mapping, Chat persistence, Deferred (cleanly shelved — the seams), INTEGRATION_GUIDE — the frozen contracts, Outbox relay (events become visible here), PlanRepository (SQLite) — the exact shapes, Reasoner (the planning LLM) (+5 more)

### Community 120 - "Metrics"
Cohesion: 0.15
Nodes (13): AIPOM — Agent Orchestrator, CLI reference, Configuration, Contributing / workflow, Documentation map, Going real, How a plan flows, HTTP API (+5 more)

### Community 121 - "Runner"
Cohesion: 0.52
Nodes (6): BaseModel, /api/runner — agent-runner configuration status.  `GET /runner/status` re-runs t, runner_status(), RunnerAgentStatus, RunnerBinaryStatus, RunnerStatusResponse

### Community 122 - "Fakes"
Cohesion: 0.18
Nodes (11): 10. One error type per file, 1. Human-review gate: `pause_after` vs `AWAITING_REVIEW`, 2. Retry classification: magic strings → typed `FailureKind`, 3. Granular redo / `reopen` (a human dislikes a good result), 4. Task result history vs single slot, 5. `required_capabilities`: ids vs names (correctness contract), 6. `Status` VO placement, 7. Generic `Entity` base (+3 more)

### Community 123 - "Control"
Cohesion: 0.29
Nodes (6): finish_review(), control — the human commands that drive the two gates.    AWAITING_REVIEW (pre-e, Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->     DISCOV, Human "finish" at the post-execution gate: REVIEW -> DONE. This is the ONLY, reopen_discovery(), PlanCompleted

### Community 124 - "Capability Matching"
Cohesion: 0.18
Nodes (11): 1. GitHub PR gate 🔀 — **DEFER, seam preserved**, 2. Project spec governance 📜 — **DEFER**, 3. Decision gate & decision history 🧭 — **DEFER but KEEP DESIGNED** (the roadmap called it "genuine whitespace"), 4. The old plan lifecycle & planner sessions 🗺 — **REPLACED** (recorded for contrast), 5. Redis event topology ⚡ — **DELETED, port-shaped seam remains**, 6. Operational machinery — **partially absorbed, partially still missing**, 7. Old agent/runtime registry — **PORTED**, Feature-by-feature record (+3 more)

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
Cohesion: 0.20
Nodes (10): Concurrency, lease, recovery (ADR-001, locked 2026-07-02), Decision log, Deferred by decision (seams preserved), Infrastructure & data (locked 2026-07-02), Mutation safety (locked 2026-07-02), Phase machine & the loop (locked 2026-07-02, Phase-0 freeze), Reasoner & planning content (locked 2026-07-03), Retry & failure (locked 2026-07-02) (+2 more)

### Community 131 - "Test Chat Repository"
Cohesion: 0.53
Nodes (5): _msg(), SqliteChatRepository: per-plan ordering, plan isolation, meta round-trip. The in, test_append_and_list_preserve_order(), test_meta_and_timestamp_round_trip(), test_plans_are_isolated()

### Community 133 - "React Router Dom"
Cohesion: 0.53
Nodes (6): getFormSubmissionInfo(), isButtonElement(), isFormDataSubmitterSupported(), isFormElement(), isHtmlElement(), isInputElement()

### Community 134 - "Dependencies"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 135 - "Engine"
Cohesion: 0.22
Nodes (9): `apply_edit` ≠ `request_replan`, ARCHITECTURE — the deliberate passthrough, ENRICHING — just-in-time task population, Invariants to defend in review, The conversational phases (multi-turn with commit), The driver model — who advances each phase, The full state machine, The plan lifecycle — the nine-phase machine (+1 more)

### Community 136 - "Main"
Cohesion: 0.40
Nodes (4): src/infra/worker/main.py — the worker entrypoint (the orchestration cadence).  W, Run the claim-and-drive loop until `stop` is set (or forever).      lease_second, run_worker_forever(), Event

### Community 137 - "Test Migrations"
Cohesion: 0.50
Nodes (4): _columns(), The fresh Alembic chain must produce the same schema the ORM metadata declares (, name -> nullable, for drift comparison., test_alembic_upgrade_head_matches_metadata()

### Community 140 - "Navigation"
Cohesion: 0.15
Nodes (18): _goal_ready(), next_action(), datetime, Goal, NextAction, Derive the next actionable unit by scanning statuses at time `now`.      `now` i, one_task_plan(), Tests proving the durable backoff gate (retry_not_before) works — including the (+10 more)

### Community 141 - "Main"
Cohesion: 0.22
Nodes (8): bump-minor-pre-major, extra-files, include-v-in-tag, initial-version, package-name, packages, release-type, $schema

### Community 148 - "Container"
Cohesion: 0.22
Nodes (9): 1. 🔥 H1 — Close the double-execution window, 2. 🔥 H2 — Poisoned-plan starvation, Deferred features — shelved with designed seams [LEG], Do-not-do list [EVO], Later — evidence-gated capability work, Next — observability, the 3am fixes [EVO Phase 2], Now — safety hotfixes [EVO Phase 1], ROADMAP (+1 more)

### Community 164 - "Main"
Cohesion: 0.25
Nodes (8): Application Layer, Deep dives, Folder map, The conversational phases (conversation.py), The crash-safety choreography (ExecutionHandler), The dispatcher + handlers (advance_plan), The worker loop (run_worker.py), Who-does-what (the persistence answer)

### Community 165 - "Main"
Cohesion: 0.25
Nodes (8): Agent runners — catalog-resolved, per run, One task's execution — the two-transaction choreography, Pause, auto-pause, and resume (un-freeze #3), Retries — the shared failure taxonomy, The execution model — worker, lease, crash choreography, workspace, The pull-scan — `next_action(goals, now)`, The worker loop, The workspace — git branching as the rollback mechanism

### Community 166 - "Main"
Cohesion: 0.25
Nodes (8): Bug-magnet zones (where a change is most likely to introduce a defect), 🔥 Defects (hotfix candidates), H1 — Double-execution window: lease < task timeout, no mid-run heartbeat, H2 — Poisoned-plan starvation (single-worker head-of-line blocking), Known issues & fragile spots, Magic numbers (working, but conventions — not laws), Operational debt, Silent-death spots (crash windows an operator can't see)

### Community 201 - "Architecture overview"
Cohesion: 0.25
Nodes (8): Architecture overview, Configuration model, Process topology, Reading the code, The domain freeze, The hexagonal layers, The system in one paragraph, Where each concern lives

### Community 202 - "toast.ts"
Cohesion: 0.36
Nodes (6): ICONS, Toaster(), Toast, ToastKind, ToastState, useToastStore

### Community 203 - "API Layer"
Cohesion: 0.24
Nodes (7): API Layer, Contracts to preserve, Deep dives, Folder map, Deep dives, Folder map, Infrastructure Layer

### Community 204 - "Events"
Cohesion: 0.29
Nodes (6): Base (`base.py`), Coarse events (`outbox.py`) — transactional, Conventions, Dedup on `event_id` (at-least-once delivery), Events, Fine events (`agent_events.py`) — best-effort

### Community 205 - "Git flow and releases"
Cohesion: 0.29
Nodes (6): Branches and pull requests, Conventional Commits, Git flow and releases, Hotfixes, Release PRs, What CI enforces

### Community 206 - "Documentation"
Cohesion: 0.29
Nodes (7): Architecture — how it works, Decisions — why it works that way, Documentation, History — the paper trail, Keeping docs honest, Legacy — the old backend's features, Structure

### Community 207 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 208 - "Events & observability"
Cohesion: 0.33
Nodes (6): Delivery: the outbox relay → SSE, Events & observability, Secrets hygiene, Structured logging, The two streams, What an operator can see today

### Community 209 - "Playwright E2E Plan — Architecture Phase Workflow (deferred)"
Cohesion: 0.33
Nodes (6): Assertions checklist, Environment constraints (learned this session — design around these), Goal, Implementation steps, Playwright E2E Plan — Architecture Phase Workflow (deferred), What already works (verified this session, no UI)

### Community 210 - "export_openapi.py"
Cohesion: 0.50
Nodes (4): _canonicalize(), main(), scripts/export_openapi.py — Dump the FastAPI OpenAPI schema to a file.  Used by, Make set-derived schema defaults deterministic across Python processes.

### Community 211 - "ADR: Concurrency model — the per-plan lease IS the unit of parallelism"
Cohesion: 0.40
Nodes (5): ADR: Concurrency model — the per-plan lease IS the unit of parallelism, Decision, Related locked decisions, What moving the lease down requires (the intentional seam), Why sequential now

### Community 212 - "History — the paper trail"
Cohesion: 0.40
Nodes (5): analyses/ — raw debugging sessions (old backend), Conventions, History — the paper trail, planning/ — the plans, in order, pre-refactor/ — the old documentation set, verbatim

### Community 213 - "register_exception_handlers"
Cohesion: 0.67
Nodes (3): FastAPI, src/api/exceptions.py — the ONE error -> HTTP mapping layer (roadmap 4.1).  Rout, register_exception_handlers()

### Community 214 - "`PlanRepository` — persistence **and** the concurrency primitives"
Cohesion: 0.50
Nodes (3): `PlanRepository` — persistence **and** the concurrency primitives, Repositories (Ports), Why the lease lives on the repo, not the aggregate

### Community 215 - "Tests"
Cohesion: 0.50
Nodes (4): Conventions, Layout, Tests, The truth test — the suite's keystone

### Community 216 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 217 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 218 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

## Ambiguous Edges - Review These
- `Orchestrator CI Workflow` → `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: references
- `Orchestrator CI Workflow` → `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: conceptually_related_to

## Knowledge Gaps
- **574 isolated node(s):** `agent-orchestrator`, `type`, `name`, `private`, `version` (+569 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **168 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Orchestrator CI Workflow` and `Dry-Run + Stub Default Mode`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `Orchestrator CI Workflow` and `Do-Not-Do List (rejected improvements)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `react` connect `React Router Components Vendor` to `React Router Vendor Core`, `App Shell & Chat Panel`, `React Router Dom`, `React Router Dom`, `Chunk E55Nsntn`, `React Router Dom`, `Package`, `React Router Dom`?**
  _High betweenness centrality (0.149) - this node is a cross-community bridge._
- **Why does `ConsoleDock()` connect `App Shell & Chat Panel` to `React Router Components Vendor`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Why does `Task` connect `Plans API Router` to `Lifecycle`, `Replan Use Case`, `Navigation`, `Domain Error Hierarchy`, `Agent Events & Task Results`, `Plan Creation & Use-Case Tests`, `Reference Data Repositories`, `Dummy Runner & Outbox Fakes`, `Plan Edit Service`, `Reasoner Config & CLI`, `Navigation Scan & Aggregate Tests`, `Pause/Resume Gate`, `Fake Clock & In-Memory Repos`, `Advance-Plan Dispatcher & Goal Entity`, `Execution Handler Running Loop`, `Test Conversation And Planning`, `Task`, `Test Transitions`, `Context`, `Test Runner Taxonomy`, `Task`, `Conversation`, `Test Worker Loop`, `Engine`, `Test Reasoner Backoff`, `Agent Errors`, `Test Openai Reasoner`, `Agent Port`, `Test Advance Plan`, `Tables`, `Test Full Cycle Llm`, `Tasks Errors`, `Agent Factory`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Are the 46 inferred relationships involving `AppContainer` (e.g. with `ConfigValue` and `AgentMetrics`) actually correct?**
  _`AppContainer` has 46 INFERRED edges - model-reasoned connections that need verification._
- **Are the 80 inferred relationships involving `Plan` (e.g. with `PhaseHandler` and `Signal`) actually correct?**
  _`Plan` has 80 INFERRED edges - model-reasoned connections that need verification._