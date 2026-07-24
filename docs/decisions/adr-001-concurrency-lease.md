# ADR: Concurrency model — the per-plan lease IS the unit of parallelism

Status: **implemented at goal granularity, symmetric leases
(2026-07-23, domain unfreeze #14)**. Originally accepted 2026-07-02
(Phase-0 domain freeze) as a seam, not yet built; first implemented
additively 2026-07-22 (unfreeze #13, one privileged plan-level goal +
a second goal-lease for the rest); made fully symmetric 2026-07-23
(unfreeze #14) after a live walkthrough of #13 surfaced both a real
concurrency bug in that asymmetry and three deliberate compromises the
user asked to remove outright — see "What changed in #14" below.
Task-granularity remains explicitly out of scope (ROADMAP's do-not-do
list: "breaks the plan-document CAS model for a speculative gain") —
this ADR's own table already named that tradeoff.

## Decision

Originally: the orchestrator ran **sequentially per plan** — one worker owns
a plan at a time via the lease (`PlanRepository.claim_one_unit / heartbeat /
release`); within a claimed plan, `next_action` returns exactly ONE ready
unit and the worker drives units one at a time.

**2026-07-22 (unfreeze #13, superseded by #14 below):** the plan-level lease
still drove planning/gates/legacy execution AND the single earliest-ready
goal (a lone worker process's behavior was byte-identical to before the
unfreeze), while a second, purely additive lease existed at goal granularity
for every OTHER ready goal. This asymmetry required the goal-lease scan to
explicitly exclude the plan-level goal to avoid colliding with it — and a
live walkthrough with two real worker processes found that exclusion was
briefly missing its full scope, causing a genuine duplicate-dispatch race
(two real agent runs for the identical task).

**As of 2026-07-23 (unfreeze #14)**, the plan-level lease no longer
dispatches execution at all for a cyclic plan — it drives only planning
(JIT enrichment fan-out) and the "every goal terminal → enter review"
transition (`app/use_cases/advance_plan.py::PlanDispatcher.advance`). EVERY
ready, already-enriched goal — including the position-earliest one — is
claimed and driven exclusively through the goal lease
(`infra/db/goal_lease_repository.py`, table `goal_leases`;
`app/use_cases/claim_ready_goal.py` + `drive_goal` in
`app/use_cases/run_worker.py`), symmetrically, by whichever worker gets
there first. There is no more privileged goal for the scan to carve out.
Real parallelism also no longer requires an operator to hand-start a second
OS process: `infra/worker/main.py::run_worker_forever` runs its own bounded
in-process goal-worker pool (`max_concurrent_goals`, default 4) alongside
the plan-level tick, so a single `orchestrate worker start` process drives
multiple independent goals concurrently on its own; running additional
processes remains supported (the lease is a real cross-process DB row) for
horizontal/multi-host scaling, but is no longer required to get the
concurrency itself.

`Plan.block` also stopped being a single plan-wide scalar for a cyclic
plan's execution-time blocks — see `docs/decisions/decision-log.md`'s
unfreeze #14 entry for the `goal_blocks` per-goal design. That part is a
block-semantics change, not a lease-granularity change, but it was required
alongside the lease symmetry fix: without it, the instant any one goal
blocked, the plan-wide `status` flip to `BLOCKED` made the ENTIRE plan
unclaimable (the claim SQL filters `status = 'running'`), silently
defeating the whole point of symmetric per-goal leases for every OTHER
still-progressing goal.

The lease **granularity** is deliberately the future parallelism switch:

| Lease on | Concurrency you get | Status |
|---|---|---|
| plan | none — sequential, one worker per plan | still the default for planning/gates |
| goal | goals run concurrently, tasks within a goal sequential | **implemented, symmetric, 2026-07-23** |
| task | full task parallelism | explicitly rejected (ROADMAP do-not-do) |

## Why sequential now (historical — kept as the record of what this traded off)

- Crash recovery stays trivial: one lease, one owner, expired lease = reclaimable.
  The lease is the entire replacement for the old reconciler.
  **Resolved by:** the goal lease (`goal_leases` table) is the exact same
  expiry-based mechanism, just scoped per `(plan_id, goal_id)` instead of
  per `plan_id` — crash recovery is still trivial, just at finer grain.
- The version-CAS on the plan aggregate is the single write gate; parallel task
  writers would contend on it constantly (every finalize bumps the plan version).
  **Resolved by:** domain unfreeze #13 removed the `plan.version` equality
  check that made this contention a correctness problem (it was never the
  real fencing token — task identity was) — see the CAS-retry-safe finalize
  work (`ExecutionHandler._run_with_cas_retry`), which retries a
  transiently-stale write rather than treating a concurrent goal's version
  bump as staleness of an unrelated candidate.
- The workspace story (git branch per task) has no merge-conflict strategy yet.
  **Resolved by:** a per-cycle-id `fcntl.flock` in
  `GitBranchWorkspace._merge_goal_sync` serializes the one genuinely
  git-level contention point (two concurrent goal merges can't both check
  out the same cycle branch into a worktree); file-scope conflicts between
  concurrently-authored goals remain a real, currently-unenforced
  possibility, mitigated only by the existing reactive `goal_promotion_failure`
  block — a proactive scope-disjointness guard at enrichment time was
  deliberately deferred (see ROADMAP) as needing real usage evidence first.

## What moving the lease down required (the intentional seam) — done, 2026-07-22

1. ~~`next_action` must return a *set* of ready units instead of one~~ —
   done via **additive** functions instead of changing `next_action`'s own
   signature: `ready_goal_ids` + `action_for_goal`
   (`domain/services/navigation.py`), so legacy plans and every existing
   caller stayed byte-identical rather than requiring a signature migration.
2. ~~A workspace conflict strategy~~ — done: the `fcntl.flock` above
   (reactive) plus the existing `goal_promotion_failure` block (also
   reactive); the proactive half is deferred, see ROADMAP.
3. ~~Either aggregate-splitting (goal-level version CAS) or accepting CAS
   retry contention on the plan document~~ — took the second option:
   `_run_with_cas_retry` (`app/handlers/execution_handler.py`) retries the
   whole finalize transaction from scratch on `StaleVersionError` rather
   than splitting the aggregate. `Plan.goal_promotion_reservations` (per-goal
   dict, was a single scalar) is the one piece of state that DID need
   splitting, since it was a real per-goal mutex, not an incidental
   version-number coincidence.

Nothing else in the domain assumed sequentiality — readiness was already derived
per scan, and the claim predicate (worker-claimable phases) is orthogonal to
lease granularity, exactly as anticipated.

## Related locked decisions

- Claim predicate = phase ∈ {ARCHITECTURE, ENRICHING, RUNNING} + lease-free
  (the driver model): conversational phases and human gates are never claimed.
- The worker tick reports *progress*, not *claiming* — a claim that yields only
  `not_ready`/`paused` sleeps instead of spinning.
- Historical release behavior used heartbeats only between units. Decision 45
  amends this: active planning/execution actions now renew at one-third of the
  lease interval, while startup reconciliation respects every live claim.
