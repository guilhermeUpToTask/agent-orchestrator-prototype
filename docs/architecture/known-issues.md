# Known issues and compatibility debt

Verified against the refactored code on 2026-07-14; re-verified 2026-07-20
against merged PRs through #29, with operator-workflow/documentation drift
re-verified 2026-07-25. Fixed entries are removed; regressions live in tests
rather than remaining as warnings here.

## Lifecycle compatibility

- `PlanPhase`, the legacy conversation/control routes, and root `goals`
  remain for readable migrated plans and existing clients. Cyclic plans route
  by `PlanStatus` plus open artifacts and active-cycle state, but deleting
  the compatibility surface requires a separately versioned API removal.
- Some secondary frontend panels still display legacy phase history. The main
  status/control surface is status/gate/block/TDD-driven and explicitly
  distinguishes RUNNING, pause requested, PAUSED, WAITING, BLOCKED, and IDLE.

## Verification and publication

- The repository has `ProjectDefinition` but no richer persisted
  `ProjectSpec` containing canonical full-suite/build/type/lint/migration
  commands. Task verification executes frozen TaskContract commands and goal
  promotion requires accepted task evidence; cycle-wide verification can only
  aggregate those evidence references until ProjectSpec gains those commands.
- `open_pr` and `merge` publication dispositions record the reference of an
  operation completed by an external/operator adapter. This repository has no
  authenticated GitHub/forge publication port, and this refactor deliberately
  did not invent provider-specific push/PR behavior or perform an unauthorized
  external write.
- Execution attempts have global UUIDs and monotonic absolute numbers, but the
  execution ledger does not yet promote `run_kind` as a dedicated SQL column.
  Role identity is present in the orchestration path and separate invocations,
  prompts/specs, worktrees, run ids, and evidence.

## Operational visibility

- Lease heartbeat now runs during long actions, but the plan detail read model
  exposes the active run start rather than the promoted lease deadline and last
  heartbeat. Operational telemetry records liveness; a richer query DTO remains.
- A malformed plan that raises before any save can still be reclaimed first by
  oldest `updated_at` on a single worker. A dead-letter/operator quarantine
  policy is still needed for repeated unexpected application exceptions.
- SSE is bounded and non-durable for clients; reconnect relies on refetch.
  Relay and event-table retention remain operational work.

## Git/process cleanup

- Owned process groups are terminated and reaped on success, failure, timeout,
  discard, and stale results. Worker startup now prunes and audits worktrees, but
  a host crash can leave metadata until the next worker startup; there is no
  operator-triggered branch/checkpoint garbage-collection policy.
- Authoritative test checkpoint branch refs are retained after implementation
  forks from their immutable commit. They preserve auditability but need a
  retention policy for long-running repositories.

## Operator walkthrough and documentation

- Several current-facing descriptions still reflect superseded implementation
  details: plan-level sequential execution, the nine-phase lifecycle as current,
  `PROJECT_REPO_DIR`, the old branch hierarchy/migration head, or snapshot-only
  attempt logs. The cyclic lifecycle, project workspace resolver, goal leases,
  current migration chain, and live attempt-log route are authoritative until
  those docs and API metadata are reconciled.

## Invariants to preserve

1. No live aggregate reference crosses an agent/reasoner side effect.
2. Plan save, execution identity, and domain events share one UoW.
3. Test-author commits never reach the goal branch until independent GREEN
   verification accepts the implementation candidate.
4. Goal branches never reach the cycle branch until every task is DONE with
   accepted revision-bound evidence.
5. Resume changes availability only; targeted retry/block resolution are
   separate commands.
