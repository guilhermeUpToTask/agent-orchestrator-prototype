# First live plan review — execution, recovery, telemetry, and workspace

**Date:** 2026-07-13  
**Plan:** dd5c6486-e73b-4db2-8061-2432d08a1e67  
**Scope:** read-only review of the live API, SQLite event history, worker logs,
current source/tests, and the completed plan branch. No runtime or product code
was changed during this review.

The raw operator transcript is preserved separately in
[2026-07-13-end-to-end-analyses.md](2026-07-13-end-to-end-analyses.md).
This document is the evidence-normalized review.

## Executive verdict

The first real plan completed its lifecycle and produced a runnable CRUD API,
which proves that the main orchestration loop, real pi runner, retry path, git
rollback, human recovery gate, and review gate can work together end to end.
It also exposed several correctness and operability gaps that the dry-run tests
did not make obvious.

The observed cross-goal execution was not a race. It was the scheduler doing
exactly what the current code and regression test require: a backing-off goal
yields to any later dependency-satisfied goal. All five generated goals had
depends_on: [], so task failures caused the worker to alternate between goals.
That behavior conflicts with the product language that goals execute
sequentially and with the operator's intended goal barrier.

An explicit dependency graph is necessary, but not sufficient. A graph explains
which goals are correctness prerequisites; a separate scheduling policy decides
whether independent goals may be interleaved. The safe near-term contract is an
explicit graph plus strict_sequence execution. DAG-frontier execution should
remain disabled until goal-level concurrency and merge isolation are designed.

## Evidence snapshot

| Fact | Observed value | Interpretation |
|---|---:|---|
| Final aggregate | phase=done, version=78, iteration 1 | Full lifecycle completed |
| Roadmap | 5 goals, 19 tasks | Every task eventually reached DONE |
| Goal edges | 5 goals with depends_on: [] | The generated roadmap contained no dependency graph |
| Agent attempts | 28 started, 19 finished, 9 failed | runs counts attempts, not logical tasks |
| Failure kinds | 7 rate-limit, 2 tool-error | Matches persisted agent.failed rows |
| Planner reasoner usage | 7 sessions, 7 calls, 10,183 tokens | Matches the seven persisted llm.call rows |
| Provider sample | 60 calls, 177,163 tokens | Includes child pi agent model calls absent from orchestrator telemetry |
| Longest attempt | 330.10 seconds | Slow, not timed out; exceeded the 300-second lease |
| Runner timeout | 600 seconds | The perceived hang had not reached the timeout |
| Auto-pauses | 2 | One for each task that exhausted rate-limit retries |
| Project catalog | empty (GET /api/projects returned []) | The plan could not be bound to a project repository |
| Workspace output | one global workspace-repo, plan branch only | main remained the empty seed commit |

## Reconstructed incident timeline

Times are UTC from persisted coarse and fine events.

1. 10:46:29 — execution entered RUNNING.
2. 10:47:28 — the first task failed with tool_error and was requeued. The
   scheduler immediately ran a task from the next goal, then returned to the
   failed task after its short backoff.
3. 10:49:08 — goal 0 completed.
4. 10:52:30 — task 6e9c744c in goal 1 hit a rate limit and requeued. The
   scheduler started task 8f671ed2 in goal 2. The two tasks then alternated
   while their independent backoff gates expired.
5. 10:53:33 — 6e9c744c exhausted attempt 3; the plan auto-paused.
6. 11:00:16 — human resume retried only the FAILED task but also cleared the
   other PENDING task's backoff. The failed task's attempt counter reset to zero.
7. 11:00:48 — 8f671ed2, whose prior counter was preserved, exhausted its third
   attempt; the plan auto-paused again.
8. 11:45:26 — second human resume reset 8f671ed2 to attempt zero and cleared
   the other task's pending backoff. Both tasks then succeeded.
9. 11:46:06–11:51:36 — Create alembic/env.py ran for 330.10 seconds. The
   operator paused/resumed twice while it was in flight. Those commands only
   toggled the next-claim gate; they did not interrupt the running subprocess.
10. 12:06:08 — all goals were exhausted and the plan entered REVIEW.
11. 12:33:52 — the review was finished and the plan entered DONE.

## Findings

### F1 — Cross-goal bypass violates the intended sequential contract (high)

domain/services/navigation.py scans every non-terminal goal. When a goal's head
task is backing off, it records saw_backing_off and continues to the next
dependency-satisfied goal. The behavior is locked by
test_blocked_goal_yields_to_next_dependency_satisfied_goal.

This is internally consistent with depends_on, but contradicts all three of:

- PlanPhase.RUNNING documentation: execute goals sequentially;
- ADR-001's operator-facing phrase “sequential per plan”;
- the expected barrier: a later goal must not run before the earlier goal's last
  task succeeds.

The incident was therefore a contract mismatch, not corrupt persistence.

### F2 — Automatic planning cannot currently produce dependency edges (high)

Goal.depends_on exists and manual edits validate existence and acyclicity, but
the reasoner's submit_goals schema has no stable goal key or depends_on
property. _build_goals() generates UUIDs after the model submits the ordered
list and always leaves dependencies empty.

Calling depends_on an existing “DAG seam” overstates current capability: it is
available to manual edits but absent from the normal planning path.

### F3 — Resume conflates availability with a global retry mutation (high)

Plan.resume() performs four different actions at once:

- clears the human pause gate;
- clears the planning backoff;
- retries every FAILED task in every non-terminal goal;
- clears backoff on every PENDING task in those goals.

That broad mutation made the second goal re-enter immediately and contributed
to the alternating retry pattern. It also prevents an operator from resuming
the plan while intentionally leaving unrelated work gated.

Pause/resume and retry should be separate commands. A retry should target the
blocking task (or an explicitly selected set), while resume should only release
the availability gate.

### F4 — Manual retry reuses attempt identity (high)

Task.retry() resets attempt to zero. The next execution is therefore a1 again,
even though prior a1 rows and a prior a1 git attempt already exist. The live
trace contains exactly this reuse.

This weakens several contracts:

- (task_id, attempt, seq) is no longer a unique logical stream;
- the console cannot distinguish retry cycles by attempt number;
- git branch/worktree names are reused and forcibly reset;
- the final aggregate shows only the last retry-cycle attempt count;
- policy budget and audit identity are coupled to one mutable counter.

Use a monotonic absolute attempt/run identity plus a separate retry-cycle budget
counter. Human retry may reset the policy budget; it must never rewind identity.

### F5 — The “stuck task” was an observability gap, with a real lease hazard (high)

The Alembic task did not time out. It ran successfully for 330.10 seconds under
a 600-second subprocess timeout. The console showed only agent.started and
agent.finished, so there was no evidence of liveness for five and a half
minutes.

The same run exceeded the worker lease by 30.10 seconds. With another worker,
or a worker restart racing the live process, the plan could have been reclaimed
and the task executed twice. This is direct live confirmation of known issue H1.

Human pause is explicitly a boundary gate. The persisted events show two
pause/resume cycles during the attempt with retried_task_ids=[]; neither was
expected to cancel the process. The UI needs to distinguish “pause requested;
current attempt still running” from “quiescent and paused.”

### F6 — Agent Console is an event viewer, not a console (high)

The real CLI runner uses subprocess.run with capture_output and emits only start
plus finish/failure events (seq 0/1). Successful stdout is retained only as an
8,000-character tail in TaskResult; failure output is reduced to a 500-character
reason. pi_protocol.py explicitly says streaming is not implemented.

The frontend console serializes those event payloads. It also hydrates no plan
history on mount; its store is filled only by live agent.event SSE messages.
Refreshing the page loses console lines even though GET /agent-events exists.

The current label should be “Agent events” until NDJSON/stdout/stderr streaming
and history hydration are implemented.

### F7 — Metrics are arithmetically correct but semantically incomplete (high)

The pasted sequence is consistent with the UI's value-first tile layout. The
live API returned:

- LLM sessions: 7
- LLM calls: 7
- tokens: 10,183
- agent runs: 28
- failures: 9
- rate-limited: 7

Those numbers exactly match the stored event subset. The larger OpenRouter CSV
is measuring a different boundary: it includes model calls made internally by
the child pi processes. The orchestrator records planner reasoner usage but
receives no child-agent usage events.

The defect is naming and coverage, not SQL arithmetic. Metrics need explicit
scopes such as planner_llm, agent_llm, and combined, with provenance and coverage
indicators. Until agent usage is ingested, “LLM calls/tokens” should be labeled
“Planner LLM calls/tokens.”

### F8 — Workspace routing is global, not project-scoped (high)

The composition root constructs one GitBranchWorkspace from PROJECT_REPO_DIR,
defaulting to ~/.orchestrator/workspace-repo. Plans carry no project_id, the
create-plan request accepts only a brief, and the live project catalog was
empty. Consequently every plan shares one repository and all plan branches
accumulate there.

The desired ownership relationship is:

Plan.project_id -> ProjectDefinition -> ProjectWorkspaceResolver ->
~/.orchestrator/projects/<immutable-project-key>/repo

Use an immutable project ID or slug on disk rather than a mutable display name.

### F9 — Task-to-plan merging exposes partial goals and has no publish gate (medium)

Every successful task merges directly into plan/<plan_id>. If a later task in
the same goal fails, that goal is already partially integrated. Finishing the
plan changes domain state to DONE but does not merge the plan branch into main,
create a PR, or otherwise publish the result. The operator must discover and
checkout the plan branch manually.

A future hierarchy should stage task -> goal -> plan, then publish plan ->
project main through a review/PR gate. This is not required for the ordering
hotfix, but the branch lifecycle must be designed before enabling DAG-frontier
execution or goal parallelism.

### F10 — Successful agent completion is not equivalent to verified task truth (high)

The generated repository runs, but the branch contains material inconsistencies:

- the application uses sqlite:///app.db while Alembic uses
  sqlite:///./fastapi_crud.db;
- startup calls Base.metadata.create_all(), so the server can work without the
  migration that the plan claims to verify;
- both database files are committed;
- two empty “test migration” revisions were committed after the initial revision;
- the installed fastapi-crud script calls the package's placeholder main() and
  prints “Hello”, while python -m fastapi_crud follows a different path;
- there are no test files in the branch even though task output says “all tests
  pass”;
- README.md is empty;
- two roadmap tasks independently asked for the same database dependency.

The lifecycle proves execution completion, not acceptance-criteria completion.
Goal/task completion needs evidence-aware verification or a separate reviewer
gate before integration.

### F11 — An agent-spawned server escaped the attempt lifecycle (high)

At review time, a Uvicorn process started by the final verification task was
still running from the temporary task path on port 8001. The task runner waits
for the top-level pi process, but does not contain or reap grandchildren spawned
by agent tool commands. Workspace deletion therefore does not guarantee process
cleanup.

Attempts need process-group/container isolation and a cleanup step that proves no
descendants survive commit/discard.

## What worked

- Aggregate state, outbox events, and fine telemetry were sufficient to
  reconstruct the run precisely.
- Failed worktrees were discarded and successful attempts produced a clean,
  inspectable merge history.
- Rate-limit classification, durable backoff, auto-pause, and later human
  recovery all operated.
- The plan did not enter DONE automatically; the post-execution review gate was
  honored.
- The generated service passed a live CRUD exercise, despite the integration
  inconsistencies listed above.

## Recommended priority

1. Close H1 first: the observed 330-second attempt already exceeded the lease.
2. Freeze a corrected ordering/retry contract: strict goal barrier, targeted
   retry, and monotonic run identity.
3. Add regression tests before changing the aggregate or scheduler.
4. Bind plans to projects and route workspaces per project.
5. Add truthful liveness/output/usage telemetry and history hydration.
6. Introduce goal staging and a plan publication gate only after the correctness
   work is stable.

The detailed design and staged verification plan are in
[2026-07-13-execution-domain-refactor-strategy.md](../planning/2026-07-13-execution-domain-refactor-strategy.md).
