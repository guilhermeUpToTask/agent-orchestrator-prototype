# Execution-domain refactor strategy after the first live plan

**Status:** design proposal; no implementation  
**Origin:** first live real-run review on 2026-07-13  
**Primary evidence:**
[2026-07-13-first-live-plan-review.md](../analyses/2026-07-13-first-live-plan-review.md)

## Objective

Make execution order, retry identity, project ownership, git integration, and
runtime telemetry explicit enough that a failure cannot silently change which
work is eligible, a human retry cannot erase attempt identity, and the UI can
tell the difference between slow, backing off, paused, timed out, and dead.

Keep the nine-phase machine frozen. Strengthen the model inside RUNNING and its
adapters rather than adding workflow phases.

## Target invariants

1. Plan remains the only transition authority for goals and tasks.
2. Every plan is bound to exactly one immutable project identity before
   execution.
3. Every goal dependency is explicit, persisted, acyclic, and resolvable.
4. Scheduling policy is explicit and separate from the dependency graph.
5. The initial policy is strict_sequence: only the earliest non-terminal goal
   may advance; its backoff or failure blocks every later goal.
6. An execution attempt has a monotonic identity that is never reused, including
   after human retry.
7. Pause/resume changes availability only. Retry is an explicit targeted
   transition.
8. State and coarse events commit atomically; agent calls, git, and process
   control stay outside transactions; finalize always re-reads and re-guards.
9. A live attempt renews its lease and emits liveness independently of console
   output.
10. Successful task execution is staged below the plan integration branch until
    its goal is accepted.
11. Planner usage, agent usage, and run counts are separately named and can
    report incomplete coverage.
12. Fake and SQLite behavior remain identical for every transition and claim
    rule.

## The key model distinction

~~~mermaid
flowchart LR
    G[Goal dependency graph<br/>correctness prerequisites]
    P[Scheduling policy<br/>which ready node may run now]
    R[Retry/backoff state<br/>when that node may run]
    L[Lease/run state<br/>who owns the active attempt]
    W[Workspace integration<br/>where successful changes land]

    G --> P
    R --> P
    P --> L
    L --> W
~~~

An explicit DAG alone does not create a strict barrier. In a DAG, two goals with
no edge are independent and the current scanner may legally interleave them.
The operator's expected behavior requires both explicit dependencies and a
strict_sequence policy. dependency_frontier can be introduced later, when
goal-level leases and merge conflict handling exist.

## Proposed aggregate shape

### Plan

Add or formalize these concepts:

- project_id — immutable after the plan enters execution planning;
- execution_policy: strict_sequence or dependency_frontier, defaulting to
  strict_sequence; only the first value is enabled initially;
- paused remains the claim gate, but paused_reason becomes or is accompanied by
  a structured pause cause: source, blocking goal/task/run, failure kind, and
  operator-safe message;
- navigation remains derived; do not add a stored cursor.

project_id changes public API and persistence. It requires a deliberate domain
unfreeze, migration/default strategy, API contract regeneration, and project
delete guards for active/non-terminal plans.

### Goal

Keep depends_on, but make it part of normal plan construction:

- reasoner input uses stable local goal keys, not UUIDs unknown at submit time;
- submitted depends_on refers to those keys;
- the handler resolves keys to generated IDs in one operation;
- reject duplicates, unknown references, self-dependencies, and cycles;
- under strict_sequence, normalize the ordered roadmap into an explicit chain
  unless the user deliberately supplies a stricter graph.

Do not interpret “no edge” as “safe to bypass” while strict_sequence is active.

### Task and AttemptRun

Split audit identity from retry policy state:

- attempt_number — absolute, monotonic per task; used in events and display;
- retry_cycle — increments on human retry;
- cycle_attempt — the counter evaluated by RetryPolicy;
- run_id — globally unique attempt execution identity used for idempotency,
  telemetry, and workspace names;
- started_at, finished_at, last_heartbeat_at, outcome, and failure kind belong in
  an attempt read model or event projection, not necessarily inside aggregate
  JSON.

The existing attempt field needs a compatibility decision. Prefer migrating it
to absolute attempt_number and adding cycle_attempt; do not reset it.

### Pause, resume, and retry

Replace the combined operation with explicit commands:

- pause_plan(reason) — arm the boundary gate;
- resume_plan() — clear only the boundary gate;
- retry_task(task_id) — FAILED to PENDING, increment retry cycle, reset only the
  cycle policy counter, retain absolute history;
- optional later retry_failed_goal(goal_id) — explicit bulk operation with the
  affected task IDs returned to the caller.

Auto-pause records the blocking task/run. The default UI action can still offer
“Retry and resume,” but it should invoke two explicit domain operations in one
well-defined application transaction rather than hide global mutation in
resume().

## Navigation contract

### strict_sequence now

For goals ordered by position:

1. Skip terminal goals.
2. Select the first non-terminal goal only.
3. If its declared prerequisites are not DONE, return a dependency-blocked
   result. This should be invalid in a normalized chain but remains defensive.
4. Select only its first non-terminal task.
5. If that task is backing off, return NOT_READY; do not inspect later goals.
6. If it is FAILED, return the blocking failure; do not inspect later goals.
7. Close the goal only when all its tasks are terminal and none failed.

This changes the current regression test that explicitly permits bypass.

### dependency_frontier later

Only enable after goal-level leases and merge isolation exist. It may return a
set of ready goals whose dependencies are DONE. It must not reuse the current
single-plan lease as proof that multiple goal executors are safe.

## Project-scoped workspace design

### Ownership and layout

Recommended disk layout:

~~~text
~/.orchestrator/
└── projects/
    └── <immutable-project-id-or-slug>/
        ├── project.json
        └── repo/
~~~

Display names are mutable and may collide, so they should not be the filesystem
key unless an immutable unique slug is introduced.

Replace the singleton workspace with a resolver/factory:

~~~text
Plan.project_id
  -> ProjectRepository.get(project_id)
  -> ProjectWorkspaceResolver.for_project(project)
  -> GitBranchWorkspace(project_home / "repo")
~~~

Plan creation must require or deterministically select a project. Existing
unbound plans need a migration/default-project policy; silent routing back to a
global repository would preserve the ambiguity.

### Branch hierarchy

Near-term target:

~~~text
project main
  └── plan/<plan-id>
      └── goal/<goal-id>
          └── task/<task-id>/r<run-id>
~~~

- task success merges into the goal branch;
- goal completion/verification merges the goal branch into the plan branch;
- goal failure leaves prior plan state untouched while preserving an inspectable
  staging branch according to retention policy;
- plan review publishes through an explicit merge or PR gate;
- a plan reaching DONE must have a recorded output disposition: merged, pr_open,
  kept_branch, or discarded.

This hierarchy should be deferred until ordering/retry identity and per-project
routing are stable. Direct task-to-plan merging may remain temporarily, but its
partial-goal semantics must be visible.

## Runtime and telemetry design

### Lease and liveness

H1 is independent and should land first:

- heartbeat while the agent call is running, not only between units;
- enforce effective lease greater than timeout plus a safety margin;
- expose active run start, last heartbeat, deadline, and elapsed duration;
- reclaim only when lease and run-liveness rules agree.

Timeout must emit a terminal event for that run before retry policy is applied.
A killed worker relies on lease expiry, but the next worker should mark the prior
run abandoned/reclaimed rather than pretending the new execution is the same
attempt.

### True agent console

Replace the one-shot captured-output path for runtimes that support streaming:

- launch in an owned process group;
- parse verified pi NDJSON when available;
- emit bounded agent.stdout, agent.stderr, agent.tool, agent.heartbeat, and
  agent.usage chunks with monotonic sequence numbers and run_id;
- redact secrets before persistence;
- enforce per-run and retention limits;
- hydrate ConsoleDock from GET /agent-events, then tail SSE from its last ID;
- label the current UI “Agent events” until this contract exists.

Process-group teardown on commit/discard/timeout must prove no descendant remains.

### Metrics contract

Expose provenance explicitly:

~~~text
planner_llm: sessions, calls, prompt/completion/reasoning/total tokens
agent_llm:   sessions, calls, prompt/completion/reasoning/total tokens
agent_runs:  started, running, finished, failed, timed_out, cancelled
coverage:    planner=complete|partial, agent=complete|partial|unavailable
~~~

Do not present a combined total unless the time window, provider scope, and
deduplication rule are known. External provider CSV reconciliation should be an
operator tool, not an implicit comparison against differently scoped counters.

## Verification strategy

Write regression tests before changing production behavior.

### Domain/navigation

- backing-off head in goal 0 blocks goal 1 under strict_sequence;
- terminal failure in goal 0 blocks every later goal;
- explicit chain and branched DAG validate; unknown/self/cycle reject;
- the reasoner schema round-trips stable keys and dependencies;
- a replan preserves prior terminal goals and validates only the new iteration;
- no stored cursor is introduced.

### Retry/pause

- human retry never decreases absolute attempt number;
- two retry cycles produce distinct run_ids and workspace names;
- targeted retry changes only the requested task;
- resume does not clear unrelated task backoffs;
- pause during an in-flight run reports pending/quiescent state and the run
  finalizes under the existing tolerant-finalize rules;
- auto-pause state and its event commit atomically.

### Worker/runtime

- a scripted 700-second run under a nominal 300-second lease executes once with
  two workers and deterministic clock advancement;
- timeout emits agent.failed(kind=timeout) and the correct retry transition;
- killed worker -> lease expiry -> prior run marked abandoned -> new unique run;
- child process spawned by a task is terminated during attempt cleanup;
- no sleeps in tests; use FakeClock and scripted runners.

### Workspace

- two projects with two plans never share refs or files;
- retries start from the correct goal staging branch;
- failed tasks and failed goals do not mutate their parent integration branch;
- plan publication records its disposition;
- legacy unbound-plan migration is deterministic.

### API/frontend/contracts

- plan create/read carries project_id and execution policy;
- reasoner-generated and manually edited dependencies render identically;
- console hydrates history, deduplicates SSE, and preserves run ordering;
- metrics labels match their backend scope and coverage;
- regenerate OpenAPI and frontend types deterministically; build frontend.

Final gates remain the orchestration dual-backend suite, git workspace
integration tests, worker crash/lease tests, API contract tests, generated type
drift check, frontend build, and make check. The paid LLM smoke remains opt-in.

## Delivery sequence

### Phase 0 — contract decision and deliberate domain unfreeze

- Decide strict_sequence versus independent-goal bypass; recommendation:
  strict_sequence now.
- Decide targeted retry API and compatibility behavior for resume.
- Decide immutable project filesystem key and legacy default project.
- Record the domain unfreeze in docs/decisions/decision-log.md before code.

### Phase 1 — safety and characterization

- Fix H1 lease/heartbeat first.
- Add failing regression tests for cross-goal bypass and attempt reuse.
- Add live-run fixtures that reproduce the two-task rate-limit alternation.

### Phase 2 — aggregate/navigation/retry

- Introduce explicit scheduling policy and dependency validation.
- Extend reasoner schema with stable goal keys and edges.
- Separate resume from targeted retry.
- Introduce monotonic attempt/run identity and compatibility projection.

### Phase 3 — project ownership and workspace routing

- Bind plans to projects.
- Add project workspace resolver and per-project repository layout.
- Migrate or explicitly quarantine the legacy global repository.

### Phase 4 — runtime truth and observability

- Own process groups and descendant cleanup.
- Stream verified runtime output/events.
- Add run liveness/timing and console history hydration.
- Split planner and agent usage metrics.

### Phase 5 — git integration gates

- Add goal staging branches.
- Add plan output disposition and merge/PR publication strategy.
- Only then evaluate dependency_frontier and goal-level parallelism.

## Non-goals

- Do not add Redis, Celery, Temporal, or a second workflow engine.
- Do not split goal/task state into relational tables solely for this refactor.
- Do not add a stored navigation cursor.
- Do not enable goal/task parallelism as part of the strict-order fix.
- Do not make telemetry failure roll back domain state.
- Do not treat provider dashboards as the source of aggregate transitions.

## Exit criteria

The refactor is complete when a reproduced rate-limit failure keeps every later
goal blocked, a targeted human retry creates a new monotonic run identity without
touching unrelated work, two projects produce isolated repositories, a long task
cannot outlive its lease unnoticed or leak a child process, the console shows
durable real output/liveness, and the plan review gate can state exactly where
the resulting code was published.
