# API Layer

The HTTP edge. Deliberately **thin**: routers translate requests into use-case calls and DTOs — zero business logic, zero try/except, zero direct event publishing. If a router grows an `if` about plan state, the logic belongs in a use case or the domain.

## Folder map

```
api/
├── server.py            create_app(): CORS, middleware, routers under /api, /health,
│                        and the lifespan that binds the SSE broker to the event loop
│                        and runs the OUTBOX RELAY thread (without it, events are
│                        written but never seen).
├── exceptions.py        THE one error→HTTP mapping (_STATUS_BY_CODE). Every DomainError
│                        carries a stable code; adding an error type = one table line,
│                        never a new handler. Deliberately NO blanket KeyError/ValueError
│                        mapping — an unmapped builtin is a bug and surfaces as the
│                        enveloped 500 (stack trace logged, never returned).
├── dependencies.py      get_container()/set_container() — tests inject, prod builds from env.
├── security.py          ORCHESTRATOR_API_TOKEN bearer check (open when unset).
├── middleware/           Request correlation id (X-Request-ID) bound into structlog.
├── logging/             structlog configuration.
├── schemas/             Shared DTOs (ErrorEnvelope, HealthResponse).
├── sse.py               SSEBroker — per-client asyncio.Queue fan-out; thread-safe
│                        publish (off-loop callers hop via call_soon_threadsafe).
├── outbox_relay.py      The relay thread body: undelivered outbox rows → broker →
│                        mark delivered (publish-then-mark = at-least-once); tails
│                        agent_events by cursor → "agent.event".
└── routers/
    ├── plans.py         create · list · detail · edits · the gate commands ·
    │                    discovery/replanning message turns · chat history
    ├── reference.py     capabilities / agents (+default) / providers / models /
    │                    projects CRUD — delete-guards surface as 409
    ├── config.py        two-tier config get/put/delete
    ├── reasoner.py      GET /reasoner/status — config validity without secrets
    ├── runner.py        GET /runner/status — mode, per-agent bindings, binary probes
    └── events.py        GET /events — the SSE stream (named events)
```

## Contracts to preserve

- **Routers never call the broker.** Mutations write outbox rows inside the state transaction; the relay is the only publisher. This is the transactional-event guarantee — don't shortcut it for "just one quick event".
- **Errors bubble.** A router that catches a domain error and builds its own response breaks the single-mapping invariant; fix the table instead.
- **Chat replies travel in the HTTP body** (`MessageResponse{reply, committed, phase}`); SSE carries only domain events.
- **Idempotent create**: `POST /plans` honors `Idempotency-Key` (a retried create returns the same plan id).
- **Operation IDs are stable** (`plans-create`, via `generate_unique_id_function`) — the frontend's generated types depend on them; renaming a route function is a frontend-facing change (`npm run generate:api`).

## Deep dives

Event delivery end-to-end: [`docs/architecture/events-and-observability.md`](../../../docs/architecture/events-and-observability.md). The full route→use-case table: [`backend/docs/INTEGRATION_GUIDE.md`](../../docs/INTEGRATION_GUIDE.md).
