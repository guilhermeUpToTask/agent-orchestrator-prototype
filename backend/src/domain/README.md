# Domain Layer

The pure core — and **FROZEN** (Phase-0, 2026-07-02): contracts here change only
with a deliberate, recorded un-freeze (see `docs/decisions/decision-log.md`).
**No imports from `app/`, `infra/`, or `api/`;
no `asyncio`, `sqlite`, `fastapi`, or any framework.** Everything here is plain
Python + Pydantic and is unit-testable in milliseconds with zero I/O. If you can't
construct and exercise something here without a database or network, it doesn't
belong in this layer.

## Mental model

A **Plan** is a tree: `Plan → goals → tasks`, executed sequentially. The single
most important design rule:

> **Navigation is derived, never stored.** There is no cursor. "What runs next" is
> computed every time by scanning node statuses (`next_action`). A crash (reload +
> re-scan) or an edit (insert/reorder) can never desync, because there is no stored
> pointer to desync. This is what makes the old reconciler unnecessary — unready
> work is simply never selected, so it never piles up as "pending-stuck".

## Folder map

```
domain/
├── value_objects/
│   ├── lifecycle.py             Status enum + TERMINAL set; FailureKind (the shared
│   │                            failure taxonomy)
│   └── tasks_vos.py             TaskResult (typed output + idempotency record)
├── policies/retry_policies.py   RetryPolicy: should_retry() + backoff_for() — the
│                                retry/terminal/backoff DECISION (not the mechanism)
├── entities/
│   ├── task.py                  Task — guarded self-transitions + retry_not_before gate
│   ├── goal.py                  Goal — guarded self-transitions, owns ordered tasks
│   ├── agent_spec.py            AgentSpec (capabilities, model_role)
│   ├── capability.py            Capability (own identity; grows tooling later)
│   └── ia_model.py / model_provider.py / project_definition.py  reference entities
├── aggregates/
│   └── planner_orchestrator.py  Plan — AGGREGATE ROOT (the only caller of entity
│                                transitions; enforces all invariants) + the
│                                9-phase machine and the replan loop-back
├── services/
│   ├── navigation.py            next_action(goals, now) — the derive-don't-store scan
│   ├── capability_matching.py   match_agent() — pure capability→agent matcher
│   ├── edit_service.py          structural edit rules (add/remove/reorder/requirements)
│   └── lookups.py               shared find_goal / find_task (DRY for the above)
├── events/
│   ├── base.py                  DomainEvent base (event_id for consumer dedup)
│   ├── outbox.py                COARSE events — transactional with state
│   └── agent_events.py          FINE events — best-effort telemetry, attempt-tagged
├── errors/                      one exception per failure case (see errors/README)
├── factories/                   create() (from zero, runs birth invariants) +
│                                reconstruct() (from persisted state) per entity
└── repositories/                PORTS (Protocols) — interfaces only; infra implements
```

## The two key behaviors to understand

### 1. `next_action(goals, now)` — the scan (services/navigation.py)
Returns exactly one of:
- `(goal, task)` — run this task (first non-terminal task in the first reachable goal)
- `(goal, None)` — this goal's tasks are all terminal & none failed → close it DONE
- `(goal, "GOAL_FAILED")` — a FAILED task in the goal → apply goal-failure policy
- `"NOT_READY"` — work remains but everything runnable is backing off → re-check later
- `None` — nothing left → plan complete

Readiness conditions that make a node *skipped, not stuck*:
- a goal whose `depends_on` aren't all DONE (dependency gate)
- a task whose `retry_not_before > now` (backoff gate)

`TERMINAL = {DONE, SKIPPED, FAILED}` — a FAILED task is terminal, so it's skipped,
never returned forever (this is the fix for the old infinite-loop bug).

`now` is **injected** so the scan stays pure/deterministic — never call a clock
inside the domain.

### 2. The aggregate owns transitions (aggregates/planner_orchestrator.py)
`Goal` and `Task` *have* transition methods (`start`, `complete`, `fail`,
`requeue`, `skip`) but **only `Plan` calls them.** This keeps invariant enforcement
(ordering, terminal rules, goal-failure-halts-plan, the idempotency check in
`start_task`) in one place. Transitions are guarded — an illegal transition raises
`InvalidTransitionError`, not a silent bad state.

## The advancing workflow (the worker loop)
The aggregate is a **guarded state machine, not an engine** — it never loops or
advances itself. There is no "advance" method here on purpose; an application/worker
use case turns the crank:

1. `claim_one_unit(worker_id, lease_seconds)` — claim a plan that needs work (the lease).
2. `plan.peek_next(now)` → the `next_action` scan finds the next unit.
3. Act on the result:
   - `(goal, task)` → `start_task()`, run the agent through a Port, then
     `complete_task()` / `fail_task()` / `requeue_task(not_before)`.
   - `(goal, None)` → `complete_goal()`.
   - `(goal, "GOAL_FAILED")` → `fail_goal()` (halts the plan).
   - `"NOT_READY"` → nothing runnable now; `release()` and re-check later.
   - `None` → `enter_review()` (RUNNING → REVIEW; DONE only via the human
     `finish_review()`).
4. `save()` with the version CAS; on `StaleVersionError`, reload and retry.
5. At a gate phase (AWAITING_REVIEW/REVIEW) → `release()` and stop (gates always
   pause; they are also never worker-claimable); else `heartbeat()` and loop.

There is **no cursor to advance** — step 2 recomputes the frontier every tick, so a
crash+reload or a structural edit can never desync (see "derive, never store" above).
The lease (`claim_one_unit` / `heartbeat` / `release`) is what replaces the old
reconciler: an expired lease makes a dead worker's plan reclaimable by another.

## Retry & backoff live here as DECISIONS
`RetryPolicy.should_retry(attempts, kind)` decides retry-vs-terminal on the
typed `FailureKind` taxonomy;
`backoff_for(attempt)` computes the delay. The **mechanism** (waiting) is NOT here
— backoff is expressed as the durable `task.retry_not_before` timestamp that the
scan honors. The domain decides *whether* and *how long*; it never sleeps.

## Further reading
Each sub-package carries its own `README.md` with the local design notes:
`aggregates/`, `entities/`, `services/`, `events/`, `policies/`, `repositories/`,
`value_objects/`, `factories/`, and `errors/`. The domain-design questions and how
each was resolved at the freeze live in
**[`docs/decisions/domain-design-decisions.md`](../../../docs/decisions/domain-design-decisions.md)**;
lifecycle semantics are diagrammed in
**[`docs/architecture/plan-lifecycle.md`](../../../docs/architecture/plan-lifecycle.md)**.
