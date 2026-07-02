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

### Advancing and pausing
The aggregate never advances itself — the worker loop drives it (see the domain
[`README`](../README.md#the-advancing-workflow-the-worker-loop)). `should_pause()` is a
**cooperative** check the loop runs *between* task units, never mid-run: the domain never
kills a live agent. The worker finishes the current task (reaches a terminal state),
persists, then pauses by releasing the lease. Force-killing a running agent is an
adapter/process concern, deliberately not modeled here.

`pause_after` (the human-review gate) overlaps with the `AWAITING_REVIEW` phase — see
[`../../DESIGN_NOTES.md`](../../DESIGN_NOTES.md).
