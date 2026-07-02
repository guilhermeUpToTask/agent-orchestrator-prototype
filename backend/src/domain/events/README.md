# Events

Two tiers, on purpose.

## Coarse events (`outbox.py`) — transactional
State transitions (`TaskStarted`, `TaskCompleted`, `PlanFailed`, …). Written to the outbox
in the **same transaction** as the state change (transactional outbox), so state and event
can never diverge. A relay (deferred) ships them to Redis later.

## Fine events (`agent_events.py`) — best-effort
Tool calls, steps, tokens streamed mid-run by the agent runner. **Not** transactional with
state; tagged by `attempt` so a re-run after a crash is distinguishable in the live view.

## Base (`base.py`)
- **`plan_id`** is the aggregate/stream key: every coarse event belongs to exactly one plan,
  so consumers route/partition by it and use it to fetch full state from the repo. Payloads
  stay id-only (the minimal-payload rule), so `plan_id` is the correlation handle.
- **`event_type`** derives a stable string discriminator from the class name — used for
  (de)serialization (the row stores it so the relay knows which class to rebuild) and for
  consumer routing. Deriving it from the class keeps it impossible to desync from the type.

## Dedup on `event_id` (at-least-once delivery)
An at-least-once relay can deliver the same event twice, so each consumer records processed
`event_id`s and skips repeats (effectively-once). Example:

> `TaskCompleted(event_id=X)` is written in the same DB txn as `task → DONE`. The relay ships
> `X` to Redis but crashes before marking it sent. On restart it re-ships `X`. The consumer
> sees `X` already processed and ignores it — no double side-effect.

## Conventions
- Events carry **no default log message**: they're pure typed data. Human-readable messages
  are a presentation concern the consumer/adapter formats at the log site (structlog uses
  namespaced event names there). Baking a message in would couple domain to presentation.
- An empty body (`PlanCompleted(...): pass`) is correct, not a stub: the base
  (`plan_id` + `event_id` + `occurred_at`) fully identifies it; the distinct *type* is the
  signal. Only events with extra data (e.g. `PlanFailed.reason`) add fields.
