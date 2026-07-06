# Entities

Guarded state holders. Their transition methods are **only** called by the `Plan`
aggregate (see `../aggregates/`), so all cross-entity invariants stay at the root.

## `Task`
The unit of work. Guarded self-transitions with a backoff gate.

- **`start()`** → `RUNNING`, increments `attempt`, clears the backoff gate. Accepts
  `PENDING` (fresh) or `RUNNING` (idempotent re-pick after a crash).
- **`complete(result)`** / **`fail(reason)`** / **`requeue(not_before)`** — the normal
  run outcomes. `requeue` clears `result`, preserves `attempt`, and sets the durable
  `retry_not_before` backoff gate the scan honors.
- **`skip()`** → `SKIPPED`. Marks the task terminal *without running it* — when the work
  became unnecessary (an edit removed the need, a dependency made it moot, a human/planner
  bypassed it). `SKIPPED` is terminal so the scan passes over it, and unlike `FAILED` it
  does **not** trip the goal-failure signal.
- **`is_terminal`** is a `@property` (zero-arg derived value). **`is_ready_at(now)`** is a
  *method*, not a property, because it needs `now` injected — the domain never reads the
  clock itself.
- **`required_capabilities`** are capability **ids** (references into the catalog), not
  embedded `Capability` entities — embedding would duplicate catalog data into every task
  and go stale. Note `match_agent()` compares them against `{c.id for c in agent.capabilities}`,
  so they must be ids, not names (see [`domain-design-decisions.md`](../../../../docs/decisions/domain-design-decisions.md)).
- **`result`** is a single current slot (overwritten on requeue). The audit trail of prior
  attempts lives in telemetry/events, not on the aggregate (see DESIGN_NOTES).

## `Goal`
A phase-level chunk owning an ordered task list; its status is **derived** from its tasks.
It has only `start` / `complete` / `fail` — no retry or stop, on purpose:

- Retry/backoff is a *task* concern (`RetryPolicy` + `task.requeue` + `retry_not_before`);
  a goal never "retries" — re-running a goal means requeuing its tasks.
- "Stop" is not goal-level: failing a goal halts the whole plan (`Plan.fail_goal`), and
  pausing is a worker-loop concern (the gate phases always pause).

`depends_on` is the DAG seam (unused in a straight chain).

## `AgentSpec`
`role` = the agent's functional job ("test_writer", "implementer", "reviewer").
`model_role` = an indirection key naming a model **tier** ("cheap", "smart",
"long_context") resolved against the provider catalog at runtime — so swapping the model
behind a tier doesn't touch every agent that uses it.

## `base.py`
Reserved for a shared `Entity` base (typed id + identity equality); not introduced yet —
see [`domain-design-decisions.md`](../../../../docs/decisions/domain-design-decisions.md).
