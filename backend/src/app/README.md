# Application Layer

Use cases and phase handlers that orchestrate the domain. This layer **decides WHEN things happen** — transaction boundaries, when to persist, when to emit events, the order of operations — and depends only on **ports** (Protocols). It imports `domain/` but never `infra/` or `api/`; adapters are injected in.

## Who-does-what (the persistence answer)

> The **domain** mutates state and enforces invariants (persistence-ignorant).
> The **application** (here) decides WHEN to persist and emit — it opens the
> transaction and calls `uow.plans.save` / `uow.outbox.add`.
> The **infrastructure** actually writes to SQLite, behind the ports.

The aggregate never saves itself; the use case never mutates an aggregate field directly (it calls the aggregate's guarded methods). Every write follows one shape: `plan.bump_version()` → `uow.outbox.add(event)` → `uow.plans.save(plan)`, all inside one `with uow:` block.

## Folder map

```
app/
├── ports.py                 App-specific contracts (TaskFailed, Outbox, UnitOfWork,
│                            ChatStore) + re-exports of the five DOMAIN ports
│                            (Reasoner, AgentRunner, Workspace, AgentEventSink, Clock)
│                            so use cases/adapters/tests keep one import path.
├── handlers/                One concern per phase group (see below):
│   ├── base.py              Signal enum + the PhaseHandler protocol
│   ├── execution_handler.py RUNNING — the pull-scan loop + crash choreography
│   ├── planning_handler.py  ARCHITECTURE (passthrough) + ENRICHING (JIT)
│   └── gate_handler.py      the gates — returns PAUSED unconditionally
├── use_cases/
│   ├── advance_plan.py      PlanDispatcher — thin phase→handler router (one unit of work)
│   ├── run_worker.py        worker_tick / drive_plan — claim → advance loop → release
│   ├── create_plan.py       brief → persisted plan; idempotent on request_id
│   ├── conversation.py      discovery_message / replanning_message — the chat-driven
│   │                        phases (multi-turn with commit)
│   ├── control.py           the gate commands: approve · finish · replan-from-review
│   ├── request_replan.py    mid-RUNNING entry to REPLANNING (state machinery only)
│   └── apply_edit.py        surgical structural edits (≠ request_replan)
└── testing/fakes.py         in-memory doubles: InMemoryPlanRepository (CAS + lease
                             semantics identical to SQLite), DummyAgentRunner
                             (scripted per task id, shared failure taxonomy),
                             InMemoryChatStore, FakeClock — the whole loop runs with
                             ZERO infrastructure. Infra re-exports the dummy as the
                             dry-run runtime.
```

## The dispatcher + handlers (advance_plan)

`advance_plan` routes on `plan.phase` and returns a `Signal` to the worker loop — it replaced the old god-function so task execution, planning, and gates can't disturb each other:

| Phase | Handler | Signal behavior |
|---|---|---|
| RUNNING | `ExecutionHandler` | CONTINUE per unit; NOT_READY when everything backs off; PAUSED into REVIEW |
| ARCHITECTURE / ENRICHING | `PlanningHandler` | passthrough / one-goal-JIT, CONTINUE per checkpoint |
| DISCOVERY / REPLANNING | (never worker-driven) | PAUSED — defensive; the claim predicate hides these |
| AWAITING_REVIEW / REVIEW | `GateHandler` | PAUSED **unconditionally** (the old conditional check was the verified gate-spin bug) |
| DONE / FAILED | terminal | DONE / FAILED |

## The crash-safety choreography (ExecutionHandler)

The rules every change here must preserve — each one answers a specific crash:

1. **Check-before-act idempotency** — a picked task that already has a `result` (crash after the agent ran, before finalize) is finalized WITHOUT re-running the agent.
2. **Two-transaction write** — txn1 marks RUNNING + persists + outbox; the agent side effect runs OUTSIDE any transaction; txn2 re-reads, re-guards, persists the outcome.
3. **No live aggregate refs across transaction boundaries** — txn1 snapshots plain values into the frozen `_Unit`; finalize re-reads fresh (real SQLite detaches objects on commit).
4. **Tolerant finalize** — if the plan left RUNNING mid-flight (replan), a late failure terminal-skips (never requeues into an abandoned iteration); a late success lands as harmless history.
5. **Durable backoff gate** — requeue sets `retry_not_before = now + backoff`; the scan honors it; it survives crashes and never blocks other ready work.
6. **Retry-vs-terminal is a domain decision** — `RetryPolicy.should_retry(attempt, kind)` on the shared `FailureKind` taxonomy.
7. **Agent resolved before RUNNING** — a missing agent fails fast, never strands a RUNNING task.

## The conversational phases (conversation.py)

Per message turn: guard phase on a fresh read → persist the USER message BEFORE the LLM call (own short chat txn — it survives reasoner crashes) → `reasoner.converse(...)` outside any txn → no goals = append the reply, phase unchanged; goals = re-open the plan txn, RE-GUARD (a racing human command wins), commit goals + phase + `PhaseAdvanced` atomically. Chat is display history; the plan transaction is truth — neither can roll the other back.

## The worker loop (run_worker.py)

`worker_tick`: claim (lease) → `drive_plan` (`while signal == CONTINUE: advance; heartbeat`) → release in `finally`. The tick returns **progress, not claiming** — a claim that yields zero steps returns False so the caller sleeps (the verified spin fix). Crash recovery is the lease: a dead worker's plan is reclaimed by any worker from persisted state.

## Deep dives

Lifecycle semantics: [`docs/architecture/plan-lifecycle.md`](../../../docs/architecture/plan-lifecycle.md) · execution mechanics: [`docs/architecture/execution-model.md`](../../../docs/architecture/execution-model.md) · the exact port contracts: [`backend/docs/INTEGRATION_GUIDE.md`](../../docs/INTEGRATION_GUIDE.md).
