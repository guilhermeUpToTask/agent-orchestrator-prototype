# Shared abstractions ledger

Grep-first index of reusable primitives, maintained by `/accelerate`. It exists because
independently-routed parallel branches have repeatedly reinvented the same concern on their
own — three separate "bound this output to N bytes" implementations and two separate
"parse this idempotency key" implementations landed in `src/infra/runtime/` across different
task branches before anyone noticed (2026-07-18/19 SOLID/DRY cleanup).

## Contract

- **Before writing a task packet**, grep this file (and the target module tree) for keywords
  matching the new task's objective. If a hit exists, the packet's `relevant paths` section
  MUST name the existing primitive and instruct the agent to reuse/extend it, not reimplement
  it — copy the exact "MUST REUSE" phrasing pattern used in `.claude/skills/accelerate/SKILL.md`.
- **After a task's diff is verified and merged**, scan it for new small standalone
  functions/classes that generalize past this one task (helpers with no task-specific naming,
  docstrings describing a reusable concern). Append them below. Skip anything that's
  genuinely one-off — this ledger is for primitives, not an inventory of every function.
- **When two packets in the same wave touch overlapping keywords** in overlapping subtrees,
  do not dispatch them in parallel: sequence them (one packet owns the new primitive, the
  other's packet is written to depend on and reuse it), or fold them into one packet. This is
  the actual fix for the duplication problem, not just bookkeeping after the fact.

## Index

| Concern | Keywords | Primitive | Location | Purpose |
|---|---|---|---|---|
| Bounded output retention (in-memory tail) | bound, truncat, tail, output, buffer | `_BoundedBuffer` | `backend/src/infra/runtime/process_supervisor.py` | Per-stream deque that evicts oldest whole lines once retained bytes exceed a cap; tracks true unbounded byte counts separately. |
| Bounded output retention (durable JSONL) | bound, truncat, log, jsonl, disk | `_BoundedLog` | `backend/src/infra/runtime/process_supervisor.py` | Same retention concern as `_BoundedBuffer` but persisted to disk with a `{"truncated":true}` marker; kept as a separate class deliberately (different storage semantics), not a target for merging with `_BoundedBuffer`. |
| Bounded single-line excerpt with secret redaction | tail, redact, secret, excerpt, safe | `safe_runtime_tail` | `backend/src/app/runtime_failures.py` | The ONLY place that should build a "last N chars of process output, secrets stripped" string — `taxonomy.py` and `cli_runner.py` both call this rather than slicing output themselves. |
| Subprocess spawn + streamed read + timeout/kill | subprocess, spawn, stream, timeout, kill, popen | `supervise_process` / `terminate_process_group` | `backend/src/infra/runtime/process_supervisor.py` | The shared long-running/streamed subprocess primitive. `verification_executor.py` deliberately does NOT use this (short, bounded, blocking bash commands are a different concern — see decision note in the plan history) — do not "fix" that without a design decision, it is not an oversight. |
| Process-lifecycle observation emission | observe, observation, telemetry, emit | `_emit_observation` | `backend/src/infra/runtime/process_supervisor.py` | Builds a `TelemetryObservation` from correlation IDs + a payload; call this rather than constructing `TelemetryObservation(...)` inline. |
| Agent-run event emission | emit, agent event, agentevent, sink | `CliAgentRunner._emit` | `backend/src/infra/runtime/cli_runner.py` | Builds and sends one `AgentEvent` from the correlation IDs already resolved in `run()`; any new event type added to `CliAgentRunner.run()` should go through this, not a fresh `AgentEvent(...)` literal. |
| Idempotency-key parsing | idempotency, correlation, parse key | `_parse_idempotency_key` | `backend/src/infra/runtime/cli_runner.py` | The one place that splits `plan:goal:task:run:attempt_number:attempt_id` and handles the legacy 3-part fallback. |
| Subprocess failure classification | classify, failure, kind, taxonomy, retryable | `classify_failure` / `normalize_failure` | `backend/src/infra/runtime/taxonomy.py` | `normalize_failure` already calls `classify_failure` internally and returns `.kind` on the result — never call `classify_failure` a second time just to log/branch on the kind; read it off the returned `RuntimeFailure`. |
| pi backend/env-var mapping | pi backend, env var, provider mapping | `PI_BACKEND_ENV_VAR` / `_pi_backend_for` | `backend/src/infra/runtime/cli_runner.py` (dict), `backend/src/infra/runtime/factory.py` (lookup) | Known duplicated *enumeration* (not yet fixed): `RUNTIME_TYPES`, `PI_BACKEND_ENV_VAR`, and each runner's `log_prefix` are three independently-maintained identifiers per runtime type. Flagged as a roadmap-2.4 registry redesign, not a mechanical fix — do not silently touch this from a "simplify" packet; escalate to the user first. |
| Live-socket SSE test server | sse, stream, uvicorn, live server, integration test | `live_server` fixture | `backend/tests/integration/test_sse_stream.py` | In-thread uvicorn on an ephemeral loopback port for tests that must hold a real HTTP stream open (ASGITransport/TestClient buffer responses and cannot). Reuse/extract this fixture for any future streaming/API-liveness test instead of re-deriving the threading pattern. |
| Allowlisted child-process environment | env, allowlist, scrub, child env, inherit | `_base_child_env` / `_CHILD_ENV_VARS` | `backend/src/infra/runtime/cli_runner.py` | The ONLY base environment for spawned agent subprocesses (PATH/HOME/locale/term/tmp/XDG). Never spread `**os.environ` into a child env; extend `_CHILD_ENV_VARS` deliberately if a CLI needs another var, and keep `correlation_env` as the only ORCHESTRATOR_* source. |
| Verification infrastructure-exit classification | exit code, 126, 127, command not found, infrastructure | `ExecutionHandler._raise_on_infrastructure_exit` | `backend/src/app/handlers/execution_handler.py` | The one place that decides a verification command *could not run* (126/127 → retryable `TOOL_ERROR`) vs produced a test verdict; call it before interpreting exit codes as RED/GREEN. |
| DAG cycle detection + ready-set computation | dag, dependency, graph, cycle, acyclic, ready, depends_on | `validate_acyclic` / `ready_nodes` | `backend/src/domain/services/dependency_graph.py` | Pure `dict[str, list[str]]` edge-map algorithms (no Goal/GoalOutline dependency) — lifted out of `CycleDraft.validate_dependencies`'s inline DFS (domain unfreeze #12). Any future "is this a DAG" / "what's ready given what's done" need (goal-parallelism, task-parallelism if ever revisited, frontend goal-graph rendering) should call these, not re-derive traversal logic inline. |
| Cross-process advisory locking scoped to one resource id | flock, advisory lock, cross-process, serialize, fcntl | `GitBranchWorkspace._cycle_merge_lock` | `backend/src/infra/git/workspace.py` | Per-cycle-id `fcntl.flock` under `.git/cycle-locks/<id>.lock`, held only around the critical git-plumbing window (worktree add → merge → worktree remove). The pattern (lock file under `.git/` so it never dirties working-tree status; scope the lock file name to the contended resource's id, not global) generalizes to any future cross-process serialization need in this local-first/no-Redis architecture — reuse the shape rather than re-deriving it. |
| Retry-on-optimistic-lock-conflict for a whole read-check-mutate-write transaction | stale version, cas, retry, optimistic lock, conflict, concurrent write | `ExecutionHandler._run_with_cas_retry` | `backend/src/app/handlers/execution_handler.py` | Retries a `body: Callable[[], _T]` on `StaleVersionError` up to a bounded `max_attempts` (default 5); `body` must open its OWN fresh `with uow:` transaction each call (re-fetch + re-check identity + re-apply), never close over already-fetched state — safe because `SqliteUnitOfWork.__exit__` rolls back the WHOLE transaction on any exception, so a failed attempt never partially commits. Any future "two concurrent writers, only one should win, the loser should transparently retry rather than error" need (not just plan finalize) should reuse this shape rather than hand-rolling a retry loop. |
