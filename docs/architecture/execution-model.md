# The execution model — worker, lease, crash choreography, workspace

*How tasks actually run, and why a crash at any instruction leaves the system resumable.*

Code anchors: `backend/src/app/use_cases/run_worker.py` (the loop), `backend/src/infra/db/plan_repository.py` (the lease SQL), `backend/src/app/execution_records.py` + `backend/src/infra/db/execution_record_repository.py` (stable run/attempt identity), `backend/src/app/handlers/execution_handler.py` (the two-transaction choreography), `backend/src/domain/services/navigation.py` (the scan), `backend/src/infra/git/workspace.py` (the rollback), `backend/src/infra/runtime/` (the runners).

## The worker loop

```mermaid
flowchart TB
    start([run_worker_forever]) --> claim["claim_one_unit(worker_id, lease)<br/>ONE atomic UPDATE…RETURNING:<br/>phase ∈ {architecture, enriching, running}<br/>AND (unclaimed OR lease expired)<br/>ORDER BY updated_at"]
    claim -->|no plan| sleep["sleep poll_seconds (1s)<br/>— the ONLY polling in the system"]
    sleep --> claim
    claim -->|"plan (lease acquired)"| drive

    subgraph drive["drive_plan — unit-to-unit, no waiting"]
        adv["advance_plan → dispatcher → phase handler<br/>returns a Signal"]
        hb["heartbeat (renew lease)<br/>⚠ between units only — never mid-agent-run"]
        adv --> hb
        hb -->|CONTINUE| adv
    end

    drive -->|"PAUSED (gate) · NOT_READY (all backing off)<br/>DONE · FAILED · exception"| release["release lease (finally)"]
    release --> progress{progressed?}
    progress -->|yes| claim
    progress -->|"no (claim yielded zero steps)"| sleep
```

Three hard-won behaviors are encoded here:

- **The tick reports *progress*, not *claiming*** (`worker_tick`). A claim that immediately comes back `not_ready`/`paused` with zero steps returns `False` so the caller sleeps — returning `True` there produced a verified hot claim→release CPU spin on any plan whose work was entirely backing off.
- **A tick exception never kills the worker** (`infra/worker/main.py`): log, release (the `finally` already did), sleep one poll, keep serving. ⚠ This combines badly with the claim ordering — see [known-issues.md](known-issues.md) H2 (poisoned-plan starvation).
- **Automated crash recovery is still lease-driven.** A dead worker's lease expires; any worker's next claim resumes the plan from persisted state. The execution ledger now leaves a discoverable RUNNING attempt when an unexpected worker/runtime exception escapes, but there is not yet a reconciler that closes it or proves whether a descendant process survived. Lease times are integer epochs from the injected `Clock`, so tests drive expiry deterministically with `FakeClock.advance()`.

Concurrency today is **sequential per plan** by decision ([ADR-001](../decisions/adr-001-concurrency-lease.md)): the lease is plan-granular, and its granularity (plan → goal → task) is the designed future parallelism switch.

## The pull-scan — `next_action(goals, now)`

Pure function, no stored cursor. Every RUNNING step re-derives the frontier by scanning statuses:

- **`(goal, task)`** — run this task: the goal's **head** (first non-terminal task, position order) when it is ready. Tasks in a goal are a sequential chain: a backing-off head blocks the whole goal — the scan never skips ahead to a later task (un-freeze #3, strict order).
- **`(goal, None)`** — the goal's tasks are all terminal, none failed → close it DONE.
- **`(goal, "GOAL_FAILED")`** — a FAILED task with no retries left → the execution handler **auto-pauses** the plan (`PlanPaused{auto:true}`), a recoverable halt (edit-while-paused + resume). Normally the finalize that failed the task pauses first; this scan branch is the backstop.
- **`NOT_READY`** — work exists but everything runnable is gated by an unexpired `retry_not_before` (a backing-off head blocks its goal) → release and re-check later.
- **`None`** — nothing left → RUNNING exits into REVIEW.

Readiness conditions make nodes *skipped, not stuck*: a goal whose `depends_on` aren't all DONE, and a task whose backoff hasn't expired, are simply never selected. `now` is injected — the domain never reads a clock.

## One task's execution — the two-transaction choreography

The crash-safety core. Read it as: *state transitions are transactional; side effects are not; finalize steps re-read and re-guard.*

```mermaid
sequenceDiagram
    participant H as ExecutionHandler
    participant DB as SQLite (UoW txns)
    participant WS as GitBranchWorkspace
    participant A as AgentRunner (subprocess)

    rect rgba(120,120,240,0.12)
        note over H,DB: txn1 — pick & mark
        H->>DB: re-read plan · peek_next(now)
        note over H: check-before-act: if task already has a result<br/>(crash after agent, before finalize) → finalize WITHOUT re-running
        H->>DB: resolve AgentSpec (missing agent fails fast)<br/>start_task (RUNNING, retry counter++)<br/>create/reuse execution run · create UUID attempt<br/>snapshot plain values into _Unit<br/>bump_version · outbox TaskStarted · save · COMMIT
    end

    note over H,A: — no transaction open from here —
    H->>WS: begin(plan, task, lifetime attempt number) → worktree on task/&lt;id&gt;/a&lt;n&gt;
    H->>A: run(task_snapshot, spec, correlated idempotency_key, event_sink, workspace)
    alt success
        A-->>H: TaskResult
        H->>WS: commit → --no-ff merge into plan/&lt;id&gt;
        rect rgba(120,240,120,0.12)
            note over H,DB: txn2 — finalize success
            H->>DB: re-read · tolerant-finalize check<br/>complete attempt/run SUCCEEDED<br/>complete_task · bump · outbox TaskCompleted · COMMIT
        end
    else TaskFailed(reason, kind)
        A-->>H: raises
        H->>WS: discard → worktree + branch deleted (zero trace)
        rect rgba(240,120,120,0.12)
            note over H,DB: txn2 — finalize failure
            H->>DB: re-read · tolerant-finalize check<br/>should_retry(domain retry counter, kind)?<br/>yes → attempt FAILED + run RETRYING + requeue<br/>no → attempt/run FAILED + fail_task + pause → Signal.PAUSED
        end
    end
```

The rules that make this safe, each the answer to a specific crash:

1. **Check-before-act idempotency** — a crash *after* the agent returned but *before* txn2 leaves a RUNNING task with a persisted result; the next pick finalizes it without re-invoking the agent. (⚠ The window between the *git merge* and txn2 is not covered — known-issue #2.)
2. **No live aggregate refs cross a transaction boundary** — txn1 snapshots plain values into the frozen `_Unit` dataclass; finalize transactions re-read fresh state. This is what detached-aggregate semantics on real SQLite demand.
3. **Tolerant finalize** — if the plan left RUNNING mid-flight (a replan), a late failure **terminal-skips** (never requeues into the abandoned iteration) and a late success lands as harmless history unless the finalize-abandon already closed the task.
4. **Execution identity is independent from retry policy** — a logical `run_id` spans automatic retries; each actual invocation gets a UUID `attempt_id` and task-lifetime monotonic number before side effects start. Human resume still resets `Task.attempt` for policy, but starts a new run and cannot reuse a workspace number.
5. **The backoff gate is durable** — `retry_not_before` is a persisted timestamp the scan honors, not an in-memory sleep. It survives crashes and never blocks *other goals'* ready work (a backing-off head does block its own goal — strict in-goal order).

### Pause, auto-pause, and resume (un-freeze #3)

`Plan.paused` is a durable claim gate (promoted column `plans.paused`, ANDed into the claim predicate) — not a phase. Two ways in, one way out:

- **Human pause** (`pause_plan`, allowed in the worker-claimable phases): the worker stops claiming at the next unit boundary. An attempt already in flight finalizes normally — pause latches at the unit boundary, and the finalize is CAS-protected, so there is no torn write.
- **Auto-pause**: a terminal task failure (retry budget exhausted, or a non-retryable `auth_error`/`token_limit`) does `fail_task` + `pause` in the same finalize transaction and emits `PlanPaused{auto:true}` — a *needs-attention* signal. The plan stays RUNNING+paused; the goal stays open. This replaced the old terminal `fail_goal` → FAILED (decision 18, amended).
- **Resume** (`resume_plan`) = the **manual retry** (decision 17): clears the pause gate and returns every FAILED task in a non-terminal goal to PENDING with a fresh attempt budget (`Task.retry()` — `attempt=0`, `result=None`), bypassing `should_retry`, and drops backoff gates on backing-off PENDING siblings.

While paused, goals/tasks are editable (`apply_edit` passes `plan.paused` to the edit-service guards): a RUNNING goal is editable, a FAILED task is editable/removable/rebindable, a RUNNING task never (its finalize looks it up by id). The typical recovery loop is **auto-pause → edit the offending task (or fix credentials) → resume**.

## Retries — the shared failure taxonomy

`FailureKind` is produced by **both** the real CLI runners (`infra/runtime/taxonomy.py`, conservative pattern-matching over process output — anything unrecognized is retryable `TOOL_ERROR`, because misclassifying transient-as-terminal kills a plan while the reverse merely wastes a retry) **and** the scriptable dry-run dummy — so dry-run tests exercise exactly the production retry/terminal paths.

| Kind | Classified from | Retry? |
|---|---|---|
| `connection_error` | network/DNS/socket patterns | ✅ with backoff |
| `rate_limit` | 429 / "rate limit" / "overloaded" | ✅ |
| `timeout` | subprocess `TimeoutExpired` | ✅ |
| `tool_error` | anything unrecognized, nonzero exit | ✅ |
| `token_limit` | context/max-tokens patterns | ❌ terminal — identical on every retry |
| `auth_error` | 401/403/invalid-key patterns — **also** raised for broken agent bindings | ❌ terminal — config never fixes itself mid-retry-loop |

`RetryPolicy` (persisted per plan): `max_attempts=3`, exponential backoff 2s·2ⁿ capped at 60s. The *decision* is domain; the *wait* is the scan skipping the task until the gate expires.

## Agent runners — catalog-resolved, per run

`agent_runner.mode` (SQLite config) selects globally:

- **`dry-run`** (default) — the scriptable `DummyAgentRunner` **is** the dry-run runtime; never touches the secret store.
- **`real`** — `CatalogAgentRunner` resolves **per task, per run** from the bound `AgentSpec`: `runtime_type` picks the CLI (`pi` → `pi --model … -p`, `claude` → `claude --dangerously-skip-permissions -p`, `gemini` → `gemini --yolo -p`), the provider row supplies the envelope-encrypted key (pi's backend env var derives from the provider id/name), the model row the model string. A broken binding raises terminal `TaskFailed(AUTH_ERROR)`; write-time referential checks return `AGENT_RUNNER_CONFIG_INVALID` → 422; `GET /api/runner/status` reports bindings and binary probes. Resolution per run means agent edits and key rotations apply without restarts — nothing caches a built runner.

Subprocesses are blocking, hopped off the event loop via `asyncio.to_thread`; runners know **nothing** about retries or ordering — they are a pure "execute this task now" hand. The execution handler passes `plan:goal:task:run:number:attempt` as the additive idempotency key. CLI runners preserve legacy three-part keys and, for the canonical form, inject only `ORCHESTRATOR_PLAN_ID`, `ORCHESTRATOR_GOAL_ID`, `ORCHESTRATOR_TASK_ID`, `ORCHESTRATOR_RUN_ID`, `ORCHESTRATOR_ATTEMPT_NUMBER`, and `ORCHESTRATOR_ATTEMPT_ID` into the child environment. The task prompt is a markdown contract (`build_task_prompt`); project conventions belong in the workspace's `AGENTS.md`, not in the runner.

## The workspace — git branching as the rollback mechanism

```mermaid
gitGraph
    commit id: "main"
    branch plan/P1
    commit id: "plan branch created"
    branch task/T1/a1
    commit id: "attempt 1 work"
    checkout plan/P1
    merge task/T1/a1 id: "--no-ff merge (success)"
    branch task/T2/a1
    commit id: "attempt fails" type: REVERSE
    checkout plan/P1
    branch task/T2/a2
    commit id: "retry, clean start"
    checkout plan/P1
    merge task/T2/a2 id: "--no-ff merge"
```

- `begin` → branch `task/<task_id>/a<attempt-number>` off `plan/<plan_id>` (created off the default branch on first use) + a temp worktree. The number is monotonic across the task lifetime, unlike the resettable domain retry counter. `branch -f` makes begin idempotent against a crashed prior invocation of that number.
- `commit` → commit the worktree, `--no-ff` merge into the plan branch via a throwaway merge worktree, clean up. The merge preserves the attempt in history.
- `discard` → remove worktree + delete branch. **The rollback**: a failed attempt leaves zero trace; the retry begins clean from the plan branch — stateless task execution.

`main` is never touched by plan work. GitHub PR output is deferred behind the Workspace port (see [legacy doc](../legacy/pre-refactor-backend.md)). ⚠ Orphan-debris caveats (no `worktree prune`, plan branches never GC'd) are in [known-issues.md](known-issues.md).
