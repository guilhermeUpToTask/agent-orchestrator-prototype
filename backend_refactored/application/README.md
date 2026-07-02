# Application Layer

Use cases that orchestrate the domain. This layer **decides WHEN things happen** —
transaction boundaries, when to persist, when to emit events, the order of
operations — and depends only on **ports** (interfaces). It imports from `domain/`
but never from `adapters/`/infrastructure. Adapters are injected in.

## Who-does-what (the persistence answer)

> The **domain** mutates state and enforces invariants (persistence-ignorant).
> The **application** (here) decides WHEN to persist and emit — it opens the
> transaction and calls `repo.save` / `outbox.add`.
> The **infrastructure** (not in this repo yet) actually writes to SQLite, behind
> the ports.

The aggregate never saves itself; the use case never reaches into the aggregate to
mutate a field (it calls the aggregate's methods). That separation is strict.

## Folder map

```
application/
├── ports.py                 ALL the interfaces the use cases depend on:
│                            AgentRunner, Reasoner (stub), Outbox, AgentEventSink,
│                            Workspace, Clock, UnitOfWork. (PlanRepository is the
│                            DOMAIN port — referenced, not redefined here.)
├── use_cases/
│   ├── advance_plan.py      THE worker's one unit of work (see below)
│   ├── create_plan.py       create from brief; idempotent on request_id
│   ├── apply_edit.py        structural edits via domain edit_service; version-CAS
│   ├── control.py           resume_from_review (the human-in-the-loop gate)
│   └── run_worker.py        the loop: claim → drive_plan → heartbeat → release
└── testing/fakes.py         in-memory doubles + DummyAgentRunner + FakeClock
                             (let the whole loop be tested with ZERO infrastructure)
```

## `advance_plan` — the heart (use_cases/advance_plan.py)

One unit of work; returns a control signal the worker drives on:
`"continue" | "paused" | "not_ready" | "done" | "failed"`.

It encodes every crash-safety decision:

1. **check-before-act idempotency** — if the picked task already has a `result`
   (crash after the agent ran, before the finalizing commit), finalize it WITHOUT
   re-running the agent.
2. **two-transaction write** — txn1 marks the task RUNNING and persists; the agent
   side effect runs OUTSIDE any transaction; txn2 persists result+DONE. A crash in
   the gap leaves a re-runnable RUNNING task (made safe by #1).
3. **transactional outbox** — every coarse event is `outbox.add`-ed INSIDE the
   state transaction, so state and event commit atomically (or roll back together).
4. **retry/terminal is a domain decision** — on `TaskFailed`, `policy.should_retry`
   decides requeue-vs-fail.
5. **durable backoff gate** — on requeue, set `task.retry_not_before = now +
   backoff_for(next_attempt)`. The scan skips the task until then. This survives a
   worker crash (it's persisted) and never blocks other ready work — unlike an
   in-memory sleep.
6. **atomic task semantics** — requeue discards partial work (result stays None;
   only success writes a result); the workspace is discarded on failure.
7. **agent resolved before RUNNING** — a missing agent fails fast
   (`AgentNotFoundError`) without leaving a stranded RUNNING task.

**No live aggregate references cross a transaction boundary** — plain values
(`goal_id`, `task_id`, `attempt`, a copied `retry_policy`, a copied task) are
captured before txn1 closes, so the code is correct against a real SQLite session
that detaches objects on commit.

## `run_worker` — the loop (use_cases/run_worker.py)

`worker_tick`: claim a plan (lease) → `drive_plan` → release (always, in `finally`).
`drive_plan`: `while signal == "continue": advance_plan; heartbeat`. Within a plan,
advancing is the loop — **no polling, no goal "trying to start".** `"not_ready"`
means the plan is waiting out a backoff gate → release and let a later tick re-check
once the gate (a persisted timestamp) expires. The ONLY polling is between ticks.

This is what REPLACES the old push-dispatch + reconciler:
- pending-goal noise is gone: `next_action` never selects an unready goal.
- crash recovery is the lease: a dead worker's plan is reclaimed and resumed from
  persisted state (proven by `tests/test_worker_loop.py::test_crash_recovery_via_reclaim`).

## The Clock port
Backoff needs "now". The domain scan takes `now` as an argument (stays pure); the
use cases get it from the injected `Clock` port. Real adapter is
`datetime.now(timezone.utc)`; tests use `FakeClock` to control time deterministically.

## Reasoner (stub)
`Reasoner` in ports.py is a forward declaration for the planning phases
(DRAFTING/BREAKDOWN/ENRICHING) — not yet wired into `advance_plan` (those phases
currently just pause/continue). Build it when implementing the planning phases.
