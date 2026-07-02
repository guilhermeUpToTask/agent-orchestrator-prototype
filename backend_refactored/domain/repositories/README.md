# Repositories (Ports)

Interfaces only — `Protocol`s the application and infra share; infra provides the SQLite
adapters. The domain declares the contract and depends on nothing.

## `PlanRepository` — persistence **and** the concurrency primitives
One contract (no parallel "…Like" duplicate) covering three concerns:

- **Persistence** — `get` / `save` (`save` is an optimistic-lock CAS on `version`, raising
  `StaleVersionError` on the worker-vs-edit race).
- **Create idempotency** — `find_by_request_id` / `bind_request_id` so a retried create
  returns the same plan id instead of duplicating.
- **Lease (liveness / crash recovery)** — `claim_one_unit` / `heartbeat` / `release`.

### Why the lease lives on the repo, not the aggregate
The lease is not business state — it's **cross-process coordination**: "which worker may
advance this plan right now." Implementing it needs atomic storage operations (conditional
`UPDATE`s on lease-owner/expiry columns, TTLs) and a **clock** — exactly the infrastructure
concerns the pure aggregate must not know about (the Dependency Rule; the domain never reads
a clock). The aggregate is single-process pure state + invariants; it cannot coordinate
workers. So the lease primitives sit on the persistence Port next to the row they guard, and
the adapter implements them atomically. This is the piece that replaces the old reconciler:
an expired lease makes a dead worker's plan reclaimable by another.
