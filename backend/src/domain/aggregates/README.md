# Aggregates

## `Plan` — the aggregate root
`Plan` owns the whole goal/task tree and is the **single consistency boundary**. It is
the only thing outside callers touch: they never mutate a `Goal` or `Task` directly.

### Why task transitions live on `Plan`, not on `Goal`
`Goal` and `Task` *have* guarded transition methods (`start`, `complete`, `fail`,
`requeue`, `skip`), but only `Plan` calls them. Transitions that span goal + task must
be enforced in one place, and several do:

- **Idempotency** — `start_task` returns early if `task.result is not None` (work already
  happened; don't restart it).
- **Cascade start** — starting the first task of a `PENDING` goal also starts the goal.
- **Terminal guard** — `_assert_not_terminal` blocks mutations once the plan is DONE/FAILED.
- **Goal-failure halts the plan** — `fail_goal` sets `phase = FAILED`.

If `Goal` owned task transitions, these cross-entity rules would leak or be duplicated.
Keeping them at the root is the classic DDD rule: outside callers touch only the root.

### The nine-phase machine
`PlanPhase` = DISCOVERY, REPLANNING, ARCHITECTURE, ENRICHING, AWAITING_REVIEW,
RUNNING, REVIEW, DONE, FAILED. The gates are *phases*, not a `pause_after` set
(DESIGN_NOTES #1, resolved): a plan at AWAITING_REVIEW/REVIEW always pauses the
worker and is unblocked only by a guarded human command (`approve()`,
`finish_review()`, `begin_replanning()`). Only ARCHITECTURE / ENRICHING / RUNNING
are worker-claimable (`WORKER_CLAIMABLE_PHASES` — the driver model).

### The replan loop (append-only)
`begin_replanning()` (from REVIEW or mid-RUNNING chat) skips the iteration's
PENDING work; `commit_replanned_goals(new_goals)` finalize-abandons whatever
remained non-terminal, appends the new goals after the existing positions, bumps
`iteration`, and flows into ARCHITECTURE. Prior DONE goals are never touched —
they are history and re-plan context. Late in-flight results are handled by the
tolerant finalize in the application's ExecutionHandler.

### Advancing and pausing
The aggregate never advances itself — the worker loop drives it (see the domain
[`README`](../README.md#the-advancing-workflow-the-worker-loop)). Pausing is
**cooperative**, checked by the loop *between* task units, never mid-run: the domain
never kills a live agent. The worker finishes the current task (reaches a terminal
state), persists, then pauses by releasing the lease. Force-killing a running agent
is an adapter/process concern, deliberately not modeled here.
