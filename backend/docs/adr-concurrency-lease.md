# ADR: Concurrency model — the per-plan lease IS the unit of parallelism

Status: accepted (Phase-0 domain freeze, 2026-07-02)

## Decision

The orchestrator runs **sequentially per plan**: one worker owns a plan at a time
via the lease (`PlanRepository.claim_one_unit / heartbeat / release`). Within a
claimed plan, `next_action` returns exactly ONE ready unit and the worker drives
units one at a time.

The lease **granularity** is deliberately the future parallelism switch:

| Lease on | Concurrency you get |
|---|---|
| plan (NOW) | none — sequential, one worker per plan |
| goal | goals run concurrently, tasks within a goal sequential |
| task | full task parallelism |

## Why sequential now

- Crash recovery stays trivial: one lease, one owner, expired lease = reclaimable.
  The lease is the entire replacement for the old reconciler.
- The version-CAS on the plan aggregate is the single write gate; parallel task
  writers would contend on it constantly (every finalize bumps the plan version).
- The workspace story (git branch per task) has no merge-conflict strategy yet.

## What moving the lease down requires (the intentional seam)

1. `next_action` must return a *set* of ready units instead of one (the scan
   already derives readiness statelessly, so this is an API change, not a model
   change).
2. A workspace conflict strategy (concurrent task branches merging into the same
   goal branch).
3. Either aggregate-splitting (goal-level version CAS) or accepting CAS retry
   contention on the plan document.

Nothing else in the domain assumes sequentiality — readiness is derived per scan,
and the claim predicate (worker-claimable phases) is orthogonal to lease
granularity. Record of intent: when parallelism is needed, move the lease, don't
bolt a queue on top.

## Related locked decisions

- Claim predicate = phase ∈ {ARCHITECTURE, ENRICHING, RUNNING} + lease-free
  (the driver model): conversational phases and human gates are never claimed.
- The worker tick reports *progress*, not *claiming* — a claim that yields only
  `not_ready`/`paused` sleeps instead of spinning.
- Heartbeats happen between units (never mid-agent-run), so `lease_seconds` must
  exceed the longest expected single task run until mid-run heartbeats land
  (roadmap Phase 3).
