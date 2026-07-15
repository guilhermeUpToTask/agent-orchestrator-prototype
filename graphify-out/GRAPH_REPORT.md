# Graph Report - agent-orchestrator  (2026-07-14)

## Corpus Check
- 373 files · ~475,269 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 4431 nodes · 10334 edges · 392 communities (218 shown, 174 thin omitted)
- Extraction: 74% EXTRACTED · 26% INFERRED · 0% AMBIGUOUS · INFERRED: 2698 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `dec9f4b2`
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
- invariant
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
- CapabilityRepository
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
- .create
- Services
- ADR-003: Cyclic project-plan lifecycle and deterministic execution
- getFormSubmissionInfo
- `RetryPolicy` — the retry/backoff *decision*
- change_impact.py
- verify_contracts.py
- .enrich_goal
- Value Objects
- change-matrix.md
- contract-flow.md
- doc-policy.md
- invariants.md
- migration-checklist.md
- test-map.md
- README.md
- Infrastructure Layer
- select_tests.py
- reseed_openrouter_key.sh
- start_api_and_worker.sh
- _usage
- dependencies.py
- main
- lifecycle.py
- Cyclic ProjectPlan implementation record
- Value Objects
- README.md
- .peek_next

## God Nodes (most connected - your core abstractions)
1. `Plan` - 162 edges
2. `AppContainer` - 138 edges
3. `Task` - 130 edges
4. `UnitOfWork` - 101 edges
5. `Goal` - 92 edges
6. `InvalidEditError` - 79 edges
7. `ExecutionHandler` - 75 edges
8. `AgentSpec` - 64 edges
9. `RetryPolicy` - 59 edges
10. `FakeClock` - 56 edges

## Surprising Connections (you probably didn't know these)
- `Orchestrator CI Workflow` --semantically_similar_to--> `Planned CI Pipeline (per-PR vs nightly split)`  [INFERRED] [semantically similar]
  .github/workflows/ci.yml → ROADMAP.md
- `main()` --references--> `name`  [EXTRACTED]
  plugins/agent-orchestrator-codex/scripts/validate.py → frontend/package.json
- `Orchestrator CI Workflow` --references--> `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml → README.md
- `Orchestrator CI Workflow` --conceptually_related_to--> `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml → ROADMAP.md
- `test_goal_lifecycle()` --calls--> `Goal`  [INFERRED]
  backend/tests/unit/orchestration/test_transitions.py → backend/src/domain/entities/goal.py

## Import Cycles
- None detected.

## Communities (392 total, 174 thin omitted)

### Community 0 - "Generated API Types"
Cohesion: 0.01
Nodes (295): ActiveRunResponse, AgentEventResponse, AgentMetrics, ClientOptions, ConfigDeleteValueData, ConfigDeleteValueError, ConfigDeleteValueErrors, ConfigDeleteValueResponse (+287 more)

### Community 1 - "Config & Reference API"
Cohesion: 0.24
Nodes (15): CycleDraft, IntentProposal, BaseModel, ReviewGate, test_cyclic_plan_review_edits_activation_and_publication(), cycle(), draft(), gate() (+7 more)

### Community 2 - "Frontend API Client"
Cohesion: 0.08
Nodes (42): AgentEventRow, API_TOKEN, del(), deleteConfigKey(), deleteModel(), fetchAgentEvents(), fetchPlan(), get() (+34 more)

### Community 3 - "UI Component Library"
Cohesion: 0.06
Nodes (57): Button, ButtonProps, Size, Variant, Card(), ConfirmAction(), Dialog(), Field() (+49 more)

### Community 4 - "React Router Vendor Core"
Cohesion: 0.05
Nodes (41): convertDataStrategyResultToDataResult(), DefaultErrorComponent(), findNearestBoundary(), findRedirect(), getActionDataForCommit(), getDoneFetcher(), getLoaderMatchesUntilBoundary(), getMatchesToLoad() (+33 more)

### Community 5 - "Goals Canvas & Phase Timeline"
Cohesion: 0.07
Nodes (45): DetailPanel(), GoalGroupNode(), KIND_COLOR, PhaseTimeline(), WALK, KIND_COLOR, nodeTypes, PlanCanvas() (+37 more)

### Community 6 - "Immer/Zustand Vendor"
Cohesion: 0.12
Nodes (52): applyPatches(), constructor(), createDraft(), createProxy(), createProxyProxy(), createScope(), current(), currentImpl() (+44 more)

### Community 7 - "App Shell & Chat Panel"
Cohesion: 0.08
Nodes (37): App(), PlanShell(), StaleNotice(), ChatPanel(), MODE_HINTS, ConsoleDock(), lineColor(), GatePanel() (+29 more)

### Community 8 - "Outbox Relay & SSE Events"
Cohesion: 0.15
Nodes (10): AbstractEventLoop, get_broker(), publish_sse(), src/api/sse.py — Server-Sent Events broker with per-client fan-out.  Every conne, Capture the server's event loop (call from lifespan startup)., Create and track a queue for one SSE client connection., Fan an event out to every connected client. Thread-safe., Back-compat shim over SSEBroker.publish(). (+2 more)

### Community 9 - "TanStack Query Vendor Core"
Cohesion: 0.05
Nodes (22): fetchInfiniteQuery(), getCurrentQuery(), getPreviousPageParam(), getQueries(), getResult(), hashKey(), hasObjectPrototype(), hasPreviousPage() (+14 more)

### Community 10 - "Replan Use Case"
Cohesion: 0.07
Nodes (28): IAModel, BaseModel, ModelProvider, BaseModel, CapabilityNotFoundError, Delete-guard: refuse to delete reference data still in use by something active., ReferencedEntityInUseError, ModelFactory (+20 more)

### Community 11 - "Plans API Router"
Cohesion: 0.14
Nodes (65): activate_cycle_route(), ActiveRunResponse, agent_events(), AgentEventResponse, approve_intent_route(), chat_history(), ChatMessageResponse, create() (+57 more)

### Community 12 - "Plan Creation & Use-Case Tests"
Cohesion: 0.12
Nodes (47): create_plan(), RetryPolicy, Create or return the one long-lived ProjectPlan for a project., GoalAlreadyRunningError, Edit/mutation rejected because the goal is already running or finished., catalogs(), edit(), make_agent() (+39 more)

### Community 13 - "Domain Error Hierarchy"
Cohesion: 0.08
Nodes (27): FastAPI, src/api/exceptions.py — the ONE error -> HTTP mapping layer (roadmap 4.1).  Rout, register_exception_handlers(), src/api/security.py — control-plane authentication.  Prototype-grade single shar, require_api_token(), cli(), AIPOM agent orchestrator., load_master_key() (+19 more)

### Community 14 - "Agent Events & Task Results"
Cohesion: 0.08
Nodes (25): ClaudeCodeRunner, PiAgentRunner, Runs `pi --model <m> -p "<prompt>"` in the workspace (pi-mono CLI)., Runs `claude --dangerously-skip-permissions -p "<prompt>"`., _pi_backend_for(), AgentSpec, ModelProvider, Task (+17 more)

### Community 15 - "Encrypted Secret Store"
Cohesion: 0.12
Nodes (20): BaseModel, SecretRef — a reference (URI) to a secret in the secret store.  Infra-local on p, Canonical ref for a provider's API key., SecretRef, Session, sessionmaker, Internal infra helper: the single place plaintext is unwrapped.          Used by, SqliteSecretStore (+12 more)

### Community 16 - "Git Workspace Port"
Cohesion: 0.20
Nodes (7): _git(), _git_ok(), GitBranchWorkspace, GitWorkspaceHandle, Path, src/infra/git/workspace.py — Workspace adapters (the async Workspace port).  Git, The rollback: nothing the agent did reaches the plan branch.

### Community 17 - "Provider & Model Catalog"
Cohesion: 0.09
Nodes (31): ModelNotFoundError, ModelProviderNotFoundError, Catalog-resolved: config key agent_runner.mode selects dry-run         (default,, Catalog-resolved: config key reasoner.mode selects stub (default,         no sec, _guard_model_in_use(), _model_from_row(), ModelProvider, src/infra/db/reference_repos.py — SQLite reference-data repositories.  Implement (+23 more)

### Community 18 - "Reference Data Repositories"
Cohesion: 0.06
Nodes (37): EntityAlreadyExistsError, Create rejected because an entity with this id already exists., _capability_from_row(), AgentSpec, Capability, ProjectDefinition, Non-raising read of the default-agent marker (API status reads)., Repo-level default-agent marker (not part of AgentSpec). (+29 more)

### Community 19 - "Dummy Runner & Outbox Fakes"
Cohesion: 0.16
Nodes (25): Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING., review_replan(), request_replan — enter the conversational re-plan (state machinery only).  Two e, request_replan(), agent(), goal(), harness(), _one_task_running_plan() (+17 more)

### Community 20 - "React Router Components Vendor"
Cohesion: 0.09
Nodes (36): react, Await(), convertRouteMatchToUiMatch(), createMemoryHistory(), createMemoryRouter(), createRoutesFromChildren(), DataRoutes(), _extends2() (+28 more)

### Community 21 - "LLM Client Runtime"
Cohesion: 0.17
Nodes (11): dependencies, @anthropic-ai/sdk, dagre, @fontsource/ibm-plex-mono, @fontsource/ibm-plex-sans, immer, nanoid, react-dom (+3 more)

### Community 22 - "Plan Edit Service"
Cohesion: 0.16
Nodes (25): add_task(), _assert_acyclic(), _assert_editable(), _assert_task_mutable(), edit_task_requirements(), Goal, Task, Domain service: structural edits to a plan's goals/tasks.  Lives in services (no (+17 more)

### Community 23 - "Reasoner Config & CLI"
Cohesion: 0.27
Nodes (12): AuthoritativeTestBundle, GoalContract, agent(), bundle(), AgentSpec, task_contract(), test_existing_registry_resolves_separate_roles_by_capability(), test_goal_contract_rejects_duplicate_task_ids() (+4 more)

### Community 24 - "Navigation Scan & Aggregate Tests"
Cohesion: 0.20
Nodes (31): next_action(), NextAction, Return work for only the earliest non-terminal goal.      Position is the schedu, exec_plan(), goal(), Aggregate orchestration, navigation, edits, binding, factories — the behaviors t, task(), test_advance_to_next_goal() (+23 more)

### Community 25 - "Pause/Resume Gate"
Cohesion: 0.13
Nodes (28): Clear the pause gate and requeue failed work (the manual retry): FAILED     task, resume(), pause_plan(), Manual availability controls and targeted retry., Remove only the manual pause; retry/backoff state is untouched., resume_plan(), drive(), Plan (+20 more)

### Community 26 - "Fake Clock & In-Memory Repos"
Cohesion: 0.33
Nodes (4): AgentSpec, Any, Capability, RetryPolicy

### Community 27 - "Advance-Plan Dispatcher & Goal Entity"
Cohesion: 0.09
Nodes (31): Plan, str, What one advance step tells the worker loop to do next., Signal, Goal-failure policy (amended by un-freeze #3): a goal whose remaining         wo, Plan, _next_unenriched(), PlanningHandler (+23 more)

### Community 28 - "Execution Handler Running Loop"
Cohesion: 0.14
Nodes (24): ExecutionHandler, Goal, Plan, Task, TaskResult, ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.  This is the, Re-read every identity that authorizes a branch merge/finalize., Atomically guard the candidate and reserve its Git promotion. (+16 more)

### Community 29 - "TanStack QueryClient Internals"
Cohesion: 0.12
Nodes (31): add(), build(), defaultQueryOptions(), difference(), fetchOptimistic(), functionalUpdate(), get(), getCurrentResult() (+23 more)

### Community 30 - "Planning Handler"
Cohesion: 0.09
Nodes (25): Scan exhaustion opens publication review; cyclic roots never terminate., finish_review(), control — the human commands that drive the two gates.    AWAITING_REVIEW (pre-e, Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->     DISCOV, Human "finish" at the post-execution gate: REVIEW -> DONE. This is the ONLY, reopen_discovery(), DomainEvent, BaseModel (+17 more)

### Community 31 - "Reference Repos"
Cohesion: 0.11
Nodes (20): activate_cycle(), approve_intent(), cancel_cycle_draft(), cancel_intent(), propose_intent(), OutputDisposition, ProposalKind, Application transactions for versioned intent, cycle, review, and publication. (+12 more)

### Community 32 - "Test Conversation And Planning"
Cohesion: 0.18
Nodes (27): One converse() turn. goals=None means "still conversing" (the message is     a q, ReasonerReply, _drive_planning(), goal(), handler(), plan_in(), Plan, PlanPhase (+19 more)

### Community 33 - "Test Transitions"
Cohesion: 0.11
Nodes (27): Arm the pause gate: the claim predicate skips a paused plan, so the         work, Remove a manual pause without mutating retry or backoff state., InvalidTransitionError, A state transition was attempted that the current status does not allow., test_commit_only_from_replanning(), test_set_iteration_goals_only_in_planning_phases(), mk_goal(), mk_task() (+19 more)

### Community 34 - "Readme"
Cohesion: 0.14
Nodes (6): backend/docs, Deep dives, Folder map, Infrastructure Layer, Conventions, Decisions

### Community 35 - "Context"
Cohesion: 0.12
Nodes (23): Capability, Goal, Plan, Task, Plan -> markdown context for the reasoner prompts (the old PlanningContextRender, The capability catalog as markdown — the ONLY ids task     required_capabilities, render_capabilities(), _render_live_goal() (+15 more)

### Community 36 - "Queries"
Cohesion: 0.09
Nodes (24): createAgent(), getConfigScope(), getReasonerStatus(), getRunnerStatus(), listModels(), listProviders(), setConfigKey(), useConfigScope() (+16 more)

### Community 37 - "Chunk I2Mcd6Rr"
Cohesion: 0.08
Nodes (21): NOTE: if you add a camelCased prop to this list,, NOTE: if you add a camelCased prop to this list,, NOTE: if you add a camelCased prop to this list,, TODO: When we delete legacy mode, we should make this error argument, TODO: Remove this dead flag, TODO: Remove this dead flag, TODO: Remove outdated deferRenderPhaseUpdateToNextBatch experiment. We, NOTE: This will not work correctly for non-generic events such as `change`, (+13 more)

### Community 38 - "Test Runner Taxonomy"
Cohesion: 0.07
Nodes (32): ABC, build_task_prompt(), CliAgentRunner, correlation_env(), AgentSpec, Task, TaskResult, src/infra/runtime/cli_runner.py — CLI agent runners (the async AgentRunner port) (+24 more)

### Community 39 - "@Tanstack React Query"
Cohesion: 0.13
Nodes (25): bindMethods(), canFetch(), canRun(), constructor(), continue(), createRetryer(), execute(), fetchState() (+17 more)

### Community 40 - "Support"
Cohesion: 0.05
Nodes (40): 0.1 Finish remaining domain work, 0.2 Concurrency ADR (required even though sequential now), 0.3 Settle the last domain decisions (before freeze), 1.1 Feature triage (AI-assisted, against the concrete current workflow), 1.2 Conflict list (old vs new) + resolution, 1.3 Preservation list → port map, 1.4 Data migration — CLEAN BREAK (decided), 1.5 Integration rollback — USER-HANDLED (decided) (+32 more)

### Community 41 - "Reference Repos"
Cohesion: 0.24
Nodes (10): addConsumeAwareSignal(), ensureQueryFn(), fetch(), fetchNextPage(), fetchPreviousPage(), getNextPageParam(), hasNextPage(), infiniteQueryBehavior() (+2 more)

### Community 42 - "Activity"
Cohesion: 0.05
Nodes (44): 10. Proposed contracts, 11. Correlation and trace model, 12. Runtime capability matrix, 13. Persistence and consistency model, 14. OpenTelemetry integration, 15. Privacy and security, 16. Phased implementation plan, 17. Test strategy (+36 more)

### Community 43 - "Task"
Cohesion: 0.07
Nodes (23): Derived activity; never persisted as a second lifecycle enum., BaseModel, datetime, FailureKind, Status, TaskResult, Start a new human retry cycle without reusing absolute identity., Revise executable meaning and invalidate revision-bound artifacts. (+15 more)

### Community 44 - "Conversation"
Cohesion: 0.07
Nodes (28): ChatStore, Per-plan conversation history (DISCOVERY / REPLANNING). Writes run     OUTSIDE t, _conversation_turn(), ConversationResult, discovery_message(), BaseModel, PlanPhase, conversation — the chat-driven phases (the driver model's third driver).  DISCOV (+20 more)

### Community 45 - "Planner Orchestrator"
Cohesion: 0.06
Nodes (33): 1. Discovery — `plan init`, 1. Install dependencies, 2. Architecture — `plan architect`, 2. Initialize local config, 3. Phase review — `plan review`, 3. Register at least one agent, 4. Inspecting the plan — `plan status` / `plan logs`, 4. Start the plan workflow (+25 more)

### Community 46 - "Test Api"
Cohesion: 0.10
Nodes (8): _plan_at_awaiting_review(), The thin API over TestClient: the plan lifecycle through HTTP, the error-> HTTP, Drive a stub plan discovery->architecture->enriching->awaiting_review by     com, Write agent_events rows directly through the sink for read-side tests., _seed_agent_events(), test_agent_events_read_endpoint(), test_metrics_endpoint(), test_pause_resume_and_edit_over_http()

### Community 47 - "React Router Dom"
Cohesion: 0.13
Nodes (22): callDataStrategyImpl(), callLoaderOrAction(), convertRoutesToDataRoutes(), createBrowserHistory(), createBrowserRouter(), createHashHistory(), createHashRouter(), createRouter() (+14 more)

### Community 48 - "Test Worker Loop"
Cohesion: 0.11
Nodes (12): InMemoryOutbox, InMemoryUnitOfWork, StubReasoner, _agent(), _git(), AgentSpec, Path, test_tdd_stages_and_branch_barriers_use_orchestrator_evidence() (+4 more)

### Community 49 - "Tsconfig"
Cohesion: 0.10
Nodes (20): compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules, jsx, lib, module, moduleResolution (+12 more)

### Community 50 - "React Router Dom"
Cohesion: 0.18
Nodes (21): createPath(), createSearchParams(), getInvalidPathError(), getPathContributingMatches(), getResolveToMatches(), getSearchParamsForLocation(), getTargetMatch(), hasNakedIndexQuery() (+13 more)

### Community 51 - "Engine"
Cohesion: 0.06
Nodes (58): _Claim, CollectingEventSink, DummyAgentRunner, DummyBehavior, FakeClock, _Handle, InMemoryAgentRepository, InMemoryChatStore (+50 more)

### Community 52 - "Test Full Cycle"
Cohesion: 0.27
Nodes (12): Human approval at the pre-execution gate: advance into execution., resume_from_review(), THE FULL CYCLE on the real stack (SQLite UoW + stub reasoner + dummy runner):, Tick until the worker finds nothing to progress (gates/conversational         ph, The user requests a replan WHILE a task is executing; the late failure     termi, Gate chat-back (un-freeze #3): at the pre-execution gate the user asks to     re, Pause/resume with editing while paused, and the auto-pause recovery loop:     ap, Stack (+4 more)

### Community 53 - "Planning Errors"
Cohesion: 0.08
Nodes (12): Any, Plan, Session, src/infra/db/outbox.py — SqliteOutbox (the Outbox port).  add() INSERTs on the U, SqliteOutbox, Plan, Session, sessionmaker (+4 more)

### Community 54 - "Test Edge Cases"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 55 - "Plan Repository"
Cohesion: 0.08
Nodes (23): 1.1 Per-agent consumer groups for `task.assigned` (Critical, S), 1.2 PEL recovery (High, S), 1.3 Dead code + packaging (Medium, S) — clears 10 of 14 failing tests, 1.4 Green tests + ruff (Medium), 1.5 Hotfix: discovery 409 leak (5 lines), 2.0 Spec staleness first (Medium, S), 2.1 Make the loops embeddable (stop semantics), 2.2 Extract runner functions (+15 more)

### Community 56 - "Openai Reasoner"
Cohesion: 0.11
Nodes (20): _build_task(), Any, Capability, ConversationMode, Goal, GoalOutline, Plan, Task (+12 more)

### Community 57 - "Gatepanel"
Cohesion: 0.09
Nodes (48): CyclicReviewGateActions(), PostExecutionGate(), PreExecutionGate(), RoadmapEditor(), humanize(), LifecycleRail(), activateCycle(), applyEdit() (+40 more)

### Community 58 - "@Tanstack React Query"
Cohesion: 0.14
Nodes (19): defaultMutationOptions(), find(), findAll(), getMutationDefaults(), getObserversCount(), getQueriesData(), invalidate(), invalidateQueries() (+11 more)

### Community 59 - "Gate Handler"
Cohesion: 0.25
Nodes (8): devDependencies, @hey-api/openapi-ts, @types/dagre, @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react

### Community 60 - "Test Reasoner Backoff"
Cohesion: 0.21
Nodes (14): enriching_plan(), FailingReasoner, goal(), handler(), Plan, Task, Reasoner-failure handling in the worker-driven planning phases (un-freeze #2): a, The gate is durable: an armed plan is not claimed until the clock passes it — (+6 more)

### Community 62 - "Server"
Cohesion: 0.09
Nodes (23): APIRoute, _canonicalize(), main(), scripts/export_openapi.py — Dump the FastAPI OpenAPI schema to a file.  Used by, Make set-derived schema defaults deterministic across Python processes., get_container(), get_uow(), src/api/dependencies.py — the API's dependency surface over AppContainer.  One p (+15 more)

### Community 63 - "Readme"
Cohesion: 0.15
Nodes (20): allowed_tool_names(), ArtifactCollector, build_tool_profile(), Any, Enum, str, Purpose-scoped reasoner tool profiles.  Handlers return DTO/context JSON only. T, Session-local DTO sink used by submission handlers. (+12 more)

### Community 65 - "Test Openai Reasoner"
Cohesion: 0.18
Nodes (33): OpenAIReasoner, FakeLLMClient, Any, FakeLLMClient — a scripted LLMClient for driving the agent loop and the OpenAIRe, text_turn(), tool_turn(), The tool-calling agent loop: terminal accept, {accepted:false} self-correction,, The self-correction loop: first submit rejected with errors, the model     sees (+25 more)

### Community 66 - "Test Agent Loop"
Cohesion: 0.12
Nodes (6): InMemoryCapabilityRepository, InMemoryPlanRepository, Capability, datetime, Plan, Mirrors the real adapter's contracts: detached aggregates (deep copy on     get/

### Community 67 - "React Router Dom"
Cohesion: 0.14
Nodes (7): Protocol, The workspace port: where a task attempt's file changes live.  The git adapter m, Git-branching seam. NoOp now (handle.path = shared dir); git adapter later     m, Workspace, WorkspaceHandle, LocalDirWorkspace, No isolation, no rollback — the agent works directly in the directory.

### Community 68 - "Agent Port"
Cohesion: 0.16
Nodes (7): ProjectRoutingWorkspace, ProjectWorkspaceResolver, Path, ProjectDefinition, Project-scoped workspace routing; no process-global repository fallback., Resolve plan -> immutable project -> repository for every new attempt., RoutedWorkspaceHandle

### Community 69 - "Test Advance Plan"
Cohesion: 0.12
Nodes (31): env_factory(), The truth-test parametrization: every env-based orchestration test runs against, make_plan(), End-to-end orchestration tests for advance_plan. Runs against the in-memory doub, Simulate a crash after the agent ran but before txn2: the task is RUNNING     wi, Drive the plan through RUNNING. Execution exhausts into the REVIEW gate     (pau, run_to_completion(), test_agent_events_streamed_and_tagged() (+23 more)

### Community 70 - "@Tanstack React Query"
Cohesion: 0.20
Nodes (15): addObserver(), clear(), clearGcTimeout(), clearTimeout(), destroy(), isValidTimeout(), notify(), onSubscribe() (+7 more)

### Community 71 - "@Tanstack React Query"
Cohesion: 0.24
Nodes (15): createResult(), hasListeners(), isStale(), onQueryUpdate(), resolveQueryBoolean(), setOptions(), shallowEqualObjects(), shouldFetchOn() (+7 more)

### Community 72 - "Main"
Cohesion: 0.07
Nodes (33): catch_domain_errors(), die(), err(), ok(), src/infra/cli/error_handler.py — centralised CLI error handling.  Policy:   - us, Route typed errors to stderr + exit(1); log the unexpected ones., api_start(), config() (+25 more)

### Community 74 - "Execution Model"
Cohesion: 0.20
Nodes (10): Engine policy, Referential integrity — two mechanisms, Schema at a glance, Secrets — envelope encryption, State directory, The data model, The lease (rows as ownership), The plan-as-document decision (+2 more)

### Community 75 - "Errors"
Cohesion: 0.14
Nodes (24): classify_provider_error(), _extract_provider_error_text(), provider_error_from_empty_choices(), Exception, Reasoner runtime errors + provider-error classification.  `transient` marks a fa, The planning LLM runtime could not produce a usable turn/artifact.      Subclass, Translate a raw provider API error into an actionable ReasonerError.      A tool, Build a ReasonerError for a 200 response that carries no choices.      Some Open (+16 more)

### Community 76 - "Dependency Checker"
Cohesion: 0.19
Nodes (9): check_dependencies(), DependencyReport, DepResult, _probe_binary(), src/infra/runtime/dependency_checker.py — probes the external tools the real age, Probe git + every CLI runtime binary. Never raises., Result of a single dependency probe., Aggregated result of all dependency probes. (+1 more)

### Community 77 - "@Tanstack React Query"
Cohesion: 0.19
Nodes (14): cancel(), cancelQueries(), dehydrate(), dehydrateMutation(), dehydrateQuery(), getDefaultOptions(), getMutationCache(), hydrate() (+6 more)

### Community 78 - "Request Logging"
Cohesion: 0.24
Nodes (4): datetime, OutputDisposition, Operator-only binding for quarantined legacy rows., InvalidEditError

### Community 79 - "Ports"
Cohesion: 0.10
Nodes (14): After, Architecture Improvement, Before, Files Changed, Final Assessment, Summary, Test Results, Guardrails (+6 more)

### Community 80 - "Chat Repository"
Cohesion: 0.11
Nodes (18): 🔄 Autonomous Continuous Loop, 🔧 Foundation and Orchestration Core, 🔀 GitHub PR Integration, 🎯 Goal-Driven Execution, ✅ Implemented / Substantially Present, 🌐 Long-Term, 🏗️ Mid-Term, 🚀 Near-Term (+10 more)

### Community 81 - "Planner Repo"
Cohesion: 0.08
Nodes (12): Outbox, Exception, FailureKind, Protocol, Application-layer ports + re-exports of the domain ports.  The five execution/si, Coarse domain events, added INSIDE the state transaction (transactional     outb, Raised by a Reasoner when it cannot produce a usable turn/artifact — the     pla, ReasonerUnavailable (+4 more)

### Community 82 - "Test Full Cycle Llm"
Cohesion: 0.40
Nodes (3): CapabilityFactory, Any, Capability

### Community 83 - "Test Llm Client"
Cohesion: 0.10
Nodes (32): ModelUsagePayload, ObservationConflictError, ObservationCorrelation, ObservationKind, ObservationQuality, ObservationSource, PersistedObservation, Enum (+24 more)

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
Cohesion: 0.06
Nodes (34): API/frontend/contracts, Branch hierarchy, Delivery sequence, dependency_frontier later, Domain/navigation, Execution-domain refactor strategy after the first live plan, Exit criteria, Goal (+26 more)

### Community 92 - "React Router Dom"
Cohesion: 0.18
Nodes (12): compareIndexes(), compilePath(), computeScore(), decodePath(), explodeOptionalSegments(), flattenRoutes(), matchPath(), matchRouteBranch() (+4 more)

### Community 93 - "Config"
Cohesion: 0.07
Nodes (31): Alembic migration environment for the orchestrator config/state DB.  The databas, _envelope(), add_request_id(), configure_logging(), _is_sensitive(), _mask_value(), Any, src/api/logging/config.py — structured logging + mandatory secret masking.  Sing (+23 more)

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
Cohesion: 0.24
Nodes (9): Session, sessionmaker, src/api/outbox_relay.py — delivers outbox rows to their consumers (roadmap 4.4)., One relay pass. Returns (rows delivered, new agent-events cursor)., Thread body: poll until told to stop. Own connections throughout —     never tou, relay_once(), run_outbox_relay(), Session (+1 more)

### Community 100 - "Capability Repo"
Cohesion: 0.18
Nodes (7): EmptyPlanError, PlanAlreadyTerminalError, Operation rejected because the plan is already DONE or FAILED., A plan must have a brief / cannot be created empty., RetryPolicy, test_factory_create_and_birth_invariant(), test_factory_reconstruct_roundtrip()

### Community 101 - "Model Provider Repo"
Cohesion: 0.27
Nodes (4): ModelProviderRepository, ModelProvider, Protocol, User-managed at runtime. delete() CASCADES to the provider's models     (provide

### Community 102 - "Stub Reasoner"
Cohesion: 0.07
Nodes (23): ExecutionAttempt, ExecutionAttemptStatus, ExecutionRecordRepository, ExecutionRun, ExecutionRunStatus, datetime, Enum, Protocol (+15 more)

### Community 103 - "Package"
Cohesion: 0.20
Nodes (9): name, private, scripts, build, dev, generate:api, preview, type (+1 more)

### Community 104 - "@Tanstack React Query"
Cohesion: 0.08
Nodes (50): CommandExecution, CandidateValidation, Path, Portable deterministic checks for frozen tests and task scope., sha256_file(), validate_candidate(), PlanPhase, Enum (+42 more)

### Community 105 - "Readme"
Cohesion: 0.50
Nodes (4): Orchestrator CI Workflow, Dry-Run + Stub Default Mode, Planned CI Pipeline (per-PR vs nightly split), Do-Not-Do List (rejected improvements)

### Community 107 - "Exceptions"
Cohesion: 0.27
Nodes (8): CollectingBroker, The outbox relay: undelivered rows reach the SSE broker in order and are marked, At-least-once: if marking fails after publishing, the next pass     re-delivers, sf(), test_relay_delivers_marks_and_makes_progress(), test_relay_forwards_plan_scoped_event_with_null_task_id(), test_relay_survives_crash_between_publish_and_mark(), test_relay_tails_agent_events_by_cursor()

### Community 108 - "Tasks Errors"
Cohesion: 0.04
Nodes (52): Plan, BaseModel, FailureKind, Goal, Task, TaskResult, Compatibility checkpoint while active behavior migrates to artifacts., Tolerant finalize: terminal-skip an in-flight task whose iteration was         a (+44 more)

### Community 109 - "Agent Factory"
Cohesion: 0.06
Nodes (35): PhaseHandler, Enum, Protocol, Phase handlers: one concern per phase.  advance_plan is a thin DISPATCHER that r, Handles one advance step for the phase(s) it owns. Given the plan_id and the, GateHandler, GateHandler — owns the human-gate phases (AWAITING_REVIEW, REVIEW).  A gate paus, VerificationExecutor (+27 more)

### Community 110 - "Base"
Cohesion: 0.31
Nodes (3): T, Repository ports (interfaces). Implementations live in infra and have a factory, Repository

### Community 111 - "Error Handler"
Cohesion: 0.14
Nodes (13): Context, Key risks, Stage 1 — Restore `src/domain/ports/` (structural, zero behavior), Stage 2 — Chat persistence substrate, Stage 3 — Conversational port + conversation rework + ARCHITECTURE passthrough + chat API, Stage 4 — JIT ENRICHING: per-goal task population, checkpointed, Stage 5 — LLM runtime infra (the old architecture, ported), Stage 6 — OpenAIReasoner + catalog resolution + seed CLI (+5 more)

### Community 114 - "Claude"
Cohesion: 0.22
Nodes (9): 1. `next_action(goals, now)` — the scan (services/navigation.py), 2. The aggregate owns transitions (aggregates/planner_orchestrator.py), Domain Layer, Folder map, Further reading, Mental model, Retry & backoff live here as DECISIONS, The advancing workflow (the worker loop) (+1 more)

### Community 115 - "Agent Loop"
Cohesion: 0.17
Nodes (15): _apply_pragmas(), build_engine(), make_session_factory(), Any, Engine, Session, sessionmaker, src/infra/db/engine.py — SQLite engine + session factory.  This is the single pl (+7 more)

### Community 116 - "Package"
Cohesion: 0.29
Nodes (4): LocalVerificationExecutor, Path, Portable Git diff and command execution for orchestrator-owned verification., test_changed_paths_include_agent_commits_since_task_base()

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
Cohesion: 0.12
Nodes (17): ADR-002: Runtime-neutral operational telemetry, Alternatives rejected, Boundaries, Canonical ownership, Consequences, Context, Decision, Execution identity and correlation (+9 more)

### Community 122 - "Fakes"
Cohesion: 0.18
Nodes (11): 10. One error type per file, 1. Human-review gate: `pause_after` vs `AWAITING_REVIEW`, 2. Retry classification: magic strings → typed `FailureKind`, 3. Granular redo / `reopen` (a human dislikes a good result), 4. Task result history vs single slot, 5. `required_capabilities`: ids vs names (correctness contract), 6. `Status` VO placement, 7. Generic `Entity` base (+3 more)

### Community 123 - "invariant"
Cohesion: 0.18
Nodes (16): getDataRouterConsoleError2(), invariant(), normalizeRedirectLocation(), Route(), ScrollRestoration(), stripBasename(), useDataRouterContext2(), useDataRouterState2() (+8 more)

### Community 124 - "Capability Matching"
Cohesion: 0.18
Nodes (11): 1. GitHub PR gate 🔀 — **DEFER, seam preserved**, 2. Project spec governance 📜 — **DEFER**, 3. Decision gate & decision history 🧭 — **DEFER but KEEP DESIGNED** (the roadmap called it "genuine whitespace"), 4. The old plan lifecycle & planner sessions 🗺 — **REPLACED** (recorded for contrast), 5. Redis event topology ⚡ — **DELETED, port-shaped seam remains**, 6. Operational machinery — **partially absorbed, partially still missing**, 7. Old agent/runtime registry — **PORTED**, Feature-by-feature record (+3 more)

### Community 125 - "Zustand"
Cohesion: 0.11
Nodes (18): Evidence snapshot, Executive verdict, F10 — Successful agent completion is not equivalent to verified task truth (high), F11 — An agent-spawned server escaped the attempt lifecycle (high), F1 — Cross-goal bypass violates the intended sequential contract (high), F2 — Automatic planning cannot currently produce dependency edges (high), F3 — Resume conflates availability with a global retry mutation (high), F4 — Manual retry reuses attempt identity (high) (+10 more)

### Community 127 - "Capability Factory"
Cohesion: 0.31
Nodes (4): Session, sessionmaker, Prototype-grade reference scan over non-terminal plan JSON documents., _referenced_by_active_plan()

### Community 128 - "Lifecycle"
Cohesion: 0.10
Nodes (18): ObservationRepository, Protocol, Independent append-only operational evidence repository., Return True when inserted, False for an identical duplicate., AssistantTurn, LLMClient, OpenAIChatClient, Any (+10 more)

### Community 129 - "Clock"
Cohesion: 0.40
Nodes (3): datetime, Real Clock adapter — the only place the wall clock is read for domain logic.  Th, SystemClock

### Community 130 - "Taxonomy"
Cohesion: 0.20
Nodes (10): Concurrency, lease, recovery (ADR-001, locked 2026-07-02), Decision log, Deferred by decision (seams preserved), Historical phase machine (superseded by decision 43), Infrastructure & data (locked 2026-07-02), Mutation safety (locked 2026-07-02), Reasoner & planning content (locked 2026-07-03), Retry & failure (locked 2026-07-02) (+2 more)

### Community 131 - "Test Chat Repository"
Cohesion: 0.21
Nodes (8): _parse_goals(), Capability, ConversationMode, Goal, GoalOutline, Plan, Task, src/infra/reasoner/stub_reasoner.py — deterministic Reasoner (no LLM).  Drives t

### Community 133 - "React Router Dom"
Cohesion: 0.27
Nodes (10): _git(), Path, GitBranchWorkspace on a real git repo: commit lands on the plan branch, discard, Stateless task exec: attempt 2 must not see attempt 1's discarded mess,     but, test_commit_merges_into_plan_branch(), test_cycle_task_commit_stops_at_goal_branch(), test_discard_is_a_true_rollback(), test_local_dir_workspace_hands_out_the_dir() (+2 more)

### Community 134 - "Dependencies"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 135 - "Engine"
Cohesion: 0.22
Nodes (5): Session, sessionmaker, src/infra/db/agent_event_sink.py — SqliteAgentEventSink (the AgentEventSink port, SqliteAgentEventSink, test_legacy_agent_event_is_preserved_and_marked_unknown()

### Community 136 - "Main"
Cohesion: 0.25
Nodes (8): 5. Invalid semantic version request, 6. Events endpoint timeout, Conclusion, Conclusion, Not Actually Bugs, Observation, Observation, Recommendation

### Community 137 - "Test Migrations"
Cohesion: 0.32
Nodes (5): _columns(), The fresh Alembic chain must produce the same schema the ORM metadata declares (, name -> nullable, for drift comparison., test_alembic_upgrade_head_matches_metadata(), test_upgrade_from_0007_backfills_typed_observation_metadata()

### Community 138 - "CapabilityRepository"
Cohesion: 0.27
Nodes (4): CapabilityRepository, Capability, Protocol, Capabilities have their own identity and will grow tooling relationships.     Us

### Community 140 - "Navigation"
Cohesion: 0.14
Nodes (9): BaseAppException, DomainError, Any, Exception, Base class for all domain-rule violations., Common root for every typed application error.      Subclasses set a class-level, GoalNotFoundError, TaskNotFoundError (+1 more)

### Community 141 - "Main"
Cohesion: 0.22
Nodes (8): bump-minor-pre-major, extra-files, include-v-in-tag, initial-version, package-name, packages, release-type, $schema

### Community 148 - "Container"
Cohesion: 0.17
Nodes (12): 1. 🔥 H1 — Close the double-execution window, 2. 🔥 H2 — Poisoned-plan starvation, Deferred features — shelved with designed seams [LEG], Do-not-do list [EVO], H3. 🔥 Restore a strict goal barrier [LIVE], H4. 🔥 Separate resume, retry policy, and run identity [LIVE], Later — evidence-gated capability work, Next — observability, the 3am fixes [EVO Phase 2] (+4 more)

### Community 156 - "Plans"
Cohesion: 0.40
Nodes (9): _identity(), _plan(), Stable execution run/attempt identity on both memory and SQLite UoWs., _records(), test_attempt_creation_rolls_back_with_task_start_and_outbox(), test_automatic_retry_reuses_run_and_gets_new_attempt(), test_human_retry_starts_new_run_without_reusing_attempt_number(), test_success_persists_stable_identity_before_runtime() (+1 more)

### Community 157 - "Plans"
Cohesion: 0.40
Nodes (4): Engine, db_url_for_home(), Path, Return the SQLite URL for the database under ``orchestrator_home``.

### Community 158 - "Plans"
Cohesion: 0.36
Nodes (6): AST, imported_modules(), main(), Path, literal_assignment(), main()

### Community 159 - "Plans"
Cohesion: 0.50
Nodes (3): _goal_ready(), datetime, Goal

### Community 161 - "Task"
Cohesion: 0.04
Nodes (78): ConfigValue, delete_value(), get_scope(), BaseModel, /api/config — the two-tier config store (roadmap 2.8): scope 'orchestrator' for, set_value(), AgentMetrics, LlmMetrics (+70 more)

### Community 164 - "Main"
Cohesion: 0.25
Nodes (8): Application Layer, Deep dives, Folder map, The conversational phases (conversation.py), The crash-safety choreography (ExecutionHandler), The dispatcher + handlers (advance_plan), The worker loop (run_worker.py), Who-does-what (the persistence answer)

### Community 165 - "Main"
Cohesion: 0.25
Nodes (8): Agent runners — catalog-resolved, per run, One task's execution — the two-transaction choreography, Pause, auto-pause, and resume (un-freeze #3), Retries — the shared failure taxonomy, The execution model — worker, lease, crash choreography, workspace, The pull-scan — `next_action(goals, now)`, The worker loop, The workspace — git branching as the rollback mechanism

### Community 166 - "Main"
Cohesion: 0.43
Nodes (3): LLMStack, THE FULL CYCLE driven by the REAL reasoner implementation (OpenAIReasoner) on a, test_full_cycle_on_the_real_reasoner_with_scripted_llm()

### Community 201 - "Architecture overview"
Cohesion: 0.25
Nodes (8): Architecture overview, Configuration model, Deliberate domain evolution, Process topology, Reading the code, The hexagonal layers, The system in one paragraph, Where each concern lives

### Community 202 - "toast.ts"
Cohesion: 0.15
Nodes (12): AgentSpec, Bind unbound tasks to agents by capability. Returns task ids that fell         b, AgentSpec, Enum, str, Resolve execution roles through the existing AgentSpec registry., Use the configured registry; a role capability is mandatory, never defaulted., resolve_role_agent() (+4 more)

### Community 203 - "API Layer"
Cohesion: 0.50
Nodes (4): API Layer, Contracts to preserve, Deep dives, Folder map

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
Cohesion: 0.29
Nodes (7): Delivery: the outbox relay → SSE, Events & observability, Secrets hygiene, Structured logging, The execution ledger is not a third event stream, The two streams, What an operator can see today

### Community 209 - "Playwright E2E Plan — Architecture Phase Workflow (deferred)"
Cohesion: 0.29
Nodes (6): Assertions checklist, Environment constraints (learned this session — design around these), Goal, Implementation steps, Playwright E2E Plan — Architecture Phase Workflow (deferred), What already works (verified this session, no UI)

### Community 210 - "export_openapi.py"
Cohesion: 0.29
Nodes (7): Completion and publication, Execution and verification, Legacy compatibility, Pause, retry, and failure, ProjectPlan lifecycle, Root status, Versioned planning artifacts

### Community 211 - "ADR: Concurrency model — the per-plan lease IS the unit of parallelism"
Cohesion: 0.40
Nodes (5): ADR: Concurrency model — the per-plan lease IS the unit of parallelism, Decision, Related locked decisions, What moving the lease down requires (the intentional seam), Why sequential now

### Community 212 - "History — the paper trail"
Cohesion: 0.29
Nodes (6): Advancing and pausing, Aggregates, `Plan` — the aggregate root, The nine-phase machine, The replan loop (append-only), Why task transitions live on `Plan`, not on `Goal`

### Community 213 - "register_exception_handlers"
Cohesion: 0.36
Nodes (6): ICONS, Toaster(), Toast, ToastKind, ToastState, useToastStore

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

### Community 223 - "replan_mid_running"
Cohesion: 0.29
Nodes (5): Agent runtime, Extension points, Reasoner, Shared rules, Orchestrator Runtime Adapter

### Community 355 - ".create"
Cohesion: 0.29
Nodes (7): Fix OpenAPI examples, Fix planner callback tests, High Priority, Introduce a gateway/worker architecture, Medium Priority, Recommended Next Steps, Replace global discovery state

### Community 356 - "Services"
Cohesion: 0.33
Nodes (6): Git/process cleanup, Invariants to preserve, Known issues and compatibility debt, Lifecycle compatibility, Operational visibility, Verification and publication

### Community 357 - "ADR-003: Cyclic project-plan lifecycle and deterministic execution"
Cohesion: 0.33
Nodes (5): ADR-003: Cyclic project-plan lifecycle and deterministic execution, Consequences, Context, Decision, Legacy migration

### Community 358 - "getFormSubmissionInfo"
Cohesion: 0.53
Nodes (6): getFormSubmissionInfo(), isButtonElement(), isFormDataSubmitterSupported(), isFormElement(), isHtmlElement(), isInputElement()

### Community 359 - "`RetryPolicy` — the retry/backoff *decision*"
Cohesion: 0.33
Nodes (5): `AgentSpec`, `base.py`, Entities, `Goal`, `Task`

### Community 360 - "change_impact.py"
Cohesion: 0.60
Nodes (4): changed_paths(), classify(), main(), Rule

### Community 361 - "verify_contracts.py"
Cohesion: 0.70
Nodes (4): hashes(), main(), Path, run()

### Community 362 - ".enrich_goal"
Cohesion: 0.33
Nodes (5): `capability_matching.py` — `match_agent`, `edit_service.py` — structural edits, `lookups.py` — `find_goal` / `find_task`, `navigation.py` — `next_action(goals, now)`, Services

### Community 363 - "Value Objects"
Cohesion: 0.53
Nodes (5): createPlan(), useCreatePlan(), usePlans(), useProjects(), PlansView()

### Community 372 - "Infrastructure Layer"
Cohesion: 0.53
Nodes (5): _msg(), SqliteChatRepository: per-plan ordering, plan isolation, meta round-trip. The in, test_append_and_list_preserve_order(), test_meta_and_timestamp_round_trip(), test_plans_are_isolated()

### Community 382 - "_usage"
Cohesion: 0.40
Nodes (4): Request, /api/events — the live SSE stream.  Each client gets its own broker queue (fan-o, stream_events(), StreamingResponse

### Community 383 - "dependencies.py"
Cohesion: 0.40
Nodes (4): `backoff_for(attempt)` — the `retry_index - 1` explained, `non_retryable_reasons`, Policies, `RetryPolicy` — the retry/backoff *decision*

### Community 385 - "main"
Cohesion: 0.19
Nodes (5): frontmatter(), main(), Path, run(), ValueError

### Community 386 - "lifecycle.py"
Cohesion: 0.13
Nodes (14): FailureKind, Enum, str, Lifecycle value objects shared by goals and tasks.  `Status` lives here (not in, Typed classification of a task failure. Produced by the agent runner,     consum, BaseModel, FailureKind, Typed output of a task run, and the idempotency record: if set, the work     alr (+6 more)

### Community 388 - "Cyclic ProjectPlan implementation record"
Cohesion: 0.40
Nodes (4): Cyclic ProjectPlan implementation record, Impact map, Legacy mapping, Ordered packages executed

### Community 389 - "Value Objects"
Cohesion: 0.50
Nodes (3): `Status` + `TERMINAL` (`tasks_vos.py`), `TaskResult` (`tasks_vos.py`), Value Objects

## Ambiguous Edges - Review These
- `Orchestrator CI Workflow` → `Dry-Run + Stub Default Mode`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: references
- `Orchestrator CI Workflow` → `Do-Not-Do List (rejected improvements)`  [AMBIGUOUS]
  .github/workflows/ci.yml · relation: conceptually_related_to

## Knowledge Gaps
- **696 isolated node(s):** `agent-orchestrator`, `reseed_openrouter_key.sh script`, `start_api_and_worker.sh script`, `type`, `private` (+691 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **174 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Orchestrator CI Workflow` and `Dry-Run + Stub Default Mode`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `Orchestrator CI Workflow` and `Do-Not-Do List (rejected improvements)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Task` connect `Task` to `lifecycle.py`, `Plans API Router`, `Navigation`, `Plan Creation & Use-Case Tests`, `Agent Events & Task Results`, `Provider & Model Catalog`, `Dummy Runner & Outbox Fakes`, `Reasoner Config & CLI`, `Navigation Scan & Aggregate Tests`, `Pause/Resume Gate`, `Advance-Plan Dispatcher & Goal Entity`, `Execution Handler Running Loop`, `Plans`, `Test Conversation And Planning`, `Test Transitions`, `Context`, `Test Runner Taxonomy`, `Conversation`, `Test Worker Loop`, `Engine`, `Test Reasoner Backoff`, `Test Openai Reasoner`, `Test Agent Loop`, `Test Advance Plan`, `@Tanstack React Query`, `Tasks Errors`, `Agent Factory`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Why does `react` connect `React Router Components Vendor` to `React Router Vendor Core`, `Goals Canvas & Phase Timeline`, `App Shell & Chat Panel`, `React Router Dom`, `React Router Dom`, `Chunk E55Nsntn`, `LLM Client Runtime`, `React Router Dom`, `Gatepanel`, `React Router Dom`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `ConsoleDock()` connect `App Shell & Chat Panel` to `React Router Components Vendor`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Are the 103 inferred relationships involving `Plan` (e.g. with `PhaseHandler` and `Signal`) actually correct?**
  _`Plan` has 103 INFERRED edges - model-reasoned connections that need verification._
- **Are the 56 inferred relationships involving `AppContainer` (e.g. with `ConfigValue` and `AgentMetrics`) actually correct?**
  _`AppContainer` has 56 INFERRED edges - model-reasoned connections that need verification._