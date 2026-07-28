# Phase 4.2 — operational truth — design

- **Date**: 2026-07-28
- **Status**: APPROVED, not yet implemented
- **Scope**: `src/api`, `src/infra/db`, `src/infra/worker`, `alembic` (migration
  **0016**), plus regenerated API types. **No domain change.**
- **Closes**: capability-matrix gap **G10**, the `requires_human` item the
  roadmap parks under Phase 5 item 1, and Phase 4's exit criterion *every
  advertised action works in the state that advertises it*.
- **Follows**: P4.1 (access and setup truth, merged as `fc54a41`). P4.3
  (evidence truth) follows this.

## 1. Problem

### 1.1 Nothing records that a worker is alive

Liveness exists only as `claimed_by` / `lease_expires_at` on `plans` and
`goal_leases` — both of which prove a worker is **busy**, not that it is
**running**. An idle worker holds neither, so before the first plan is claimed —
the J1/J2 setup checklist, the most common place a local install goes wrong —
"worker running, nothing to do" and "worker never started" are indistinguishable
through the API.

`GET /api/readiness` (P4.1) answers *can* this machine run a plan. It cannot
answer *is anything going to pick one up*.

### 1.2 The frontend cannot tell whose problem a block is

`src/app/block_policy.py` records `requires_human` per block kind and is the
single source for it. Nothing serves it. `PlanBlock`
(`domain/entities/planning_artifacts.py:69`) has no such field, and the domain
is frozen, so the frontend today can only render "blocked" — collapsing "a
credential is wrong, only you can fix it" into the same visual as
"`goal_promotion_failure`, which the orchestrator retries on its own". Phase 5
is asked to distinguish "waiting, recovering automatically" from "needs you"
and currently has nothing to distinguish them *with*.

### 1.3 Advertised actions carry no endpoint, and nothing checks they work

`Plan.legal_actions` publishes raw strings — `pause`, `retry_stage`,
`review:approve`, `bind_project`. `block_policy.py` maps them to routes **in a
comment**:

```python
#   retry_stage    -> POST /api/plans/{id}/retry-stage   (planning stages)
#   wait_and_retry -> POST /api/plans/{id}/retry         (clears the circuit)
```

A comment cannot be executed, and Phase 4's exit criterion — *every advertised
action works in the state that advertises it* — has no test. `review:<decision>`
is worse than the others: which endpoint serves it depends on which gate is
open, and only the server knows.

## 2. Non-goals

- **Rendering any of it.** Phase 5 owns the UI.
- **Multi-worker orchestration.** The `workers` table records what each worker
  reports about itself. Scheduling, work stealing and dead-worker takeover stay
  as they are: the lease already handles them.
- **Changing `legal_actions`' existing string vocabulary.** Clients depend on it;
  the endpoint hint is additive.
- **Evidence reads.** P4.3.

## 3. Design

### 3.1 A `workers` table, written off the event loop

Migration **0016** (`0015_plan_delete_cascade` is the current head):

```sql
CREATE TABLE workers (
    worker_id            TEXT PRIMARY KEY,
    mode                 TEXT NOT NULL,      -- agent_runner.mode at boot
    started_at           TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    poll_seconds         REAL NOT NULL,
    lease_seconds        INTEGER NOT NULL,
    max_concurrent_goals INTEGER NOT NULL,
    inflight_goals       INTEGER NOT NULL DEFAULT 0
);
```

No foreign keys: a worker is not plan-scoped, and a row must survive every plan
being deleted. Rows are upserted, never accumulated per run — a restarted
`worker-1` overwrites its own row, so the table stays the size of the fleet.

`WorkerRegistry` (`src/infra/db/worker_registry.py`) mirrors
`SqliteAgentEventSink` exactly: its own connection through `run_in_session`
(which already carries the lock-retry policy), `await asyncio.to_thread(...)`,
and **failures are logged, never propagated**. A telemetry hiccup must not kill
a worker that is otherwise working.

**Why off the loop, specifically.** `src/infra/worker/main.py` already carries
the hard-won comment that the coordinator must not perform a SQLite write while
goal tasks are in flight: a writer-lock handoff can block the event-loop thread
inside SQLite while the lock's owner is a coroutine waiting for that same loop,
which self-deadlocks. A heartbeat is a write, and it has exactly that hazard.
`asyncio.to_thread` moves the blocking wait off the loop, so the lock owner can
always resume.

### 3.2 The heartbeat is a background task, not a loop-body call

The main loop blocks in `asyncio.wait(inflight, FIRST_COMPLETED)` whenever goals
are running. A heartbeat written from the loop body would therefore go silent
for the whole of a long goal — reporting a **busy** worker as dead, which is
worse than reporting nothing.

So the heartbeat is its own `asyncio.Task`, started before the loop and
cancelled in the drain:

```python
async def _heartbeat() -> None:
    while stop is None or not stop.is_set():
        await registry.beat(worker_id, inflight_goals=len(inflight))
        await asyncio.sleep(_HEARTBEAT_SECONDS)
```

`_HEARTBEAT_SECONDS` is 5, independent of `poll_seconds` (which can be tuned to
sub-second for tests and would then produce a needless write per tick).

### 3.3 Staleness is computed by the server

`GET /api/workers` returns each row plus `stale: bool` and
`seconds_since_seen: float`, computed against the server clock — the same reason
`worker_lease.expired` is server-computed: the client's clock is not the
server's. A worker is stale at **3 missed beats** (15s), long enough to absorb a
slow tick, short enough to notice a crash within the operator's attention span.

`GET /api/readiness` gains a `workers` check: `fail` when no worker has ever
reported, `warn` when every known worker is stale (the process may be coming
back), `ok` otherwise. `fail` is right for "never started" because that is the
setup mistake; `warn` for "was here, went quiet" because a restart is normal.

### 3.4 `requires_human` via an API-layer projection

The domain block is projected, not modified:

```python
class BlockResponse(BaseModel):
    """The domain PlanBlock plus the one fact only block_policy knows."""
    id: str
    kind: str
    stage: str
    explanation: str
    goal_id: str | None
    task_id: str | None
    task_revision: int | None
    run_id: str | None
    evidence_refs: list[str]
    legal_resolutions: list[str]
    requires_human: bool
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None
```

Built by one `_block_response(block)` helper used for both `block` and
`goal_blocks` in `PlanDetailResponse`. `requires_human` comes from
`block_policy.requires_human(block.kind)` — the single source stays single.

Additive: every existing field keeps its name and type, so an existing client
that ignores the new field is unaffected.

### 3.5 `action_endpoints` on the plan document

`PlanDetailResponse` gains `action_endpoints: dict[str, str]`, parallel to
`legal_actions`, mapping each advertised action to the route that serves it:

```json
{
  "legal_actions": ["retry_stage", "edit_task", "start_replan"],
  "action_endpoints": {
    "retry_stage": "POST /api/plans/{plan_id}/retry-stage",
    "edit_task":   "POST /api/plans/{plan_id}/edits",
    "start_replan":"POST /api/plans/{plan_id}/replan"
  }
}
```

`{plan_id}` stays a template rather than being interpolated: the value is a
route identity the client already knows how to fill, and templating keeps it
comparable to `app.openapi()["paths"]` — which is what makes §3.6 possible.

`review:<decision>` resolves against the open gate's `subject_type`
(`intent` → `/intent/approve`, `cycle_draft` → `/cycle-draft/approve`,
`cycle_completion` → `/publication`), which is precisely the part a client
cannot derive.

The map is built in one function, `action_endpoints_for(plan)`, in
`src/api/routers/plans.py`. An action with no known route is **omitted rather
than guessed** — and §3.6 fails if that ever happens.

### 3.6 The contract test

Two properties, both driven by served data rather than a second table:

1. **Every advertised action has an endpoint, and that endpoint exists.** For
   plans driven into each interesting state (idle, waiting on each gate kind,
   running, paused, blocked), every entry in `legal_actions` appears in
   `action_endpoints`, and every value matches an operation in
   `app.openapi()["paths"]`.
2. **Every advertised action is accepted in the state that advertises it.** Call
   each one through its served endpoint and assert the response is **not** 404,
   405 or 422 — the failures that mean "this action was never really available".
   A 409 is legitimate (a concurrent claim) and is allowed.

This is the same shape as `test_block_policy.py`, which already asserts that
every resolution a block advertises is accepted by its route; §3.6 extends it
from block resolutions to the whole `legal_actions` vocabulary.

### 3.7 What is already served, and stays that way

The roadmap's investigate list for this area — "active action, attempts,
liveness/deadlines, live logs, capacity, and backoff" — is mostly already
covered, and the audit says so: `active_run`, `worker_lease`,
`provider_waiting`, `planning_operation.retry_at`, `GET …/attempts`,
`GET …/attempts/{id}/log[/stream]`. **Nothing here rebuilds them.** The one thing
missing was worker-level liveness, which is §3.1. This is recorded so the next
audit does not read the roadmap line as unfinished work.

## 4. Data and migrations

Migration `0016_worker_registry`, one CREATE TABLE, no backfill (an empty table
means "no worker has reported", which is exactly true before the first boot).
Down-migration drops it. No plan-scoped table, so no `ON DELETE CASCADE`
obligation — and `test_delete_plan_leaves_nothing.py` is unaffected because a
worker row is not plan-scoped.

## 5. Testing

| Area | Test |
|---|---|
| Registry | `beat` upserts (a restarted worker overwrites its own row, never duplicates); a write failure is swallowed and logged |
| Heartbeat liveness | A worker with a goal in flight keeps beating — the regression this design exists to prevent; asserted by driving the loop with a long-running fake goal |
| Staleness | Server-computed `stale`/`seconds_since_seen` against an injected clock, not wall time |
| Readiness | `fail` with no worker row, `warn` when all are stale, `ok` with a fresh one |
| `requires_human` | Served for a plan-wide block and for each `goal_blocks` entry, matching `block_policy` for every kind in the table |
| `action_endpoints` | Every `legal_actions` entry has one; every value is a real operation in the OpenAPI inventory |
| Advertised actions | Each is accepted in the state that advertises it (not 404/405/422) |
| Migration | `test_migrations.py` upgrade/downgrade round-trip, as for every prior migration |

## 6. Risks and rejected alternatives

**A heartbeat that competes with the claim path.** Mitigated three ways: off the
loop via `to_thread`, throttled to 5s regardless of `poll_seconds`, and through
`run_in_session`'s existing lock-retry. The write is a single-row upsert against
a table nothing else contends for.

**Rejected: heartbeat every tick.** `poll_seconds` defaults to 1.0 and tests
drive it far lower; the freshness gain over 5s is not worth a write per tick
against the same database the claim scan is tuned around.

**Rejected: adding `requires_human` to the domain `PlanBlock`.** It would be a
domain un-freeze for a value that is a pure function of a field the block
already carries. The policy would then exist in two places, and the persisted
copy would be the stale one after any policy change.

**Rejected: interpolating `{plan_id}` into `action_endpoints`.** Templates stay
comparable to the OpenAPI inventory, which is what lets §3.6 verify the map
instead of trusting it.

**Rejected: deriving worker health from leases only.** It is the option that
needs no migration, and it cannot see the idle worker that G10 is about.

## 7. Exit criteria

Closes, for Phase 4:

- ✅ *Every advertised action works in the state that advertises it* — §3.6.
- ✅ *Tier 0/Tier 1 need no direct SQLite edit or hidden env fallback* — the last
  piece was worker liveness; with §3.1 the setup checklist is answerable over
  HTTP alone.
- ✅ *Integration tests cover each new/corrected contract.*
- ✅ *OpenAPI describes the cyclic lifecycle; generated types current* — carried
  forward from P4.1, re-verified here.

Leaves for P4.3: the evidence read model (G9).
