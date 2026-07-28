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

- A dead worker's orphan is no longer indistinguishable from live work: plan
  detail serves `worker_lease` (scope, holder, heartbeat deadline, `expired`,
  `seconds_remaining`) alongside `active_run`, which only ever said when work
  *began*. Locked by
  `test_plan_detail_distinguishes_a_live_worker_from_a_dead_one`. The ~6 minute
  recovery latency behind it is by design and documented in ROADMAP.md with
  options — reconciliation deliberately never reverts the domain task, so
  goal-lease expiry is the only correct trigger.
- **Refusal messages mix the cyclic and legacy vocabularies.**
  `Plan.request_pause` raises `InvalidTransitionError` with `status.value`
  (`planner_orchestrator.py:395`) while `Plan.pause` and `Plan.resume` use
  `phase.value` (`:419`, `:427`). A cyclic plan the API reports as
  `status: waiting, reason: intent` is refused with "cannot transition from
  discovery to resumed" — the nine-phase vocabulary the cyclic model replaced.
  One token each, but in the FROZEN aggregate, so it needs a deliberate change
  rather than a drive-by edit.
- **No dead-letter or quarantine for a persistently failing plan.** The claim is
  now round-robin (`COALESCE(claimed_at, 0), updated_at`), so a plan that raises
  before any save can no longer monopolize the claim — that starvation is fixed
  and locked by `test_a_crashing_plan_does_not_monopolize_the_claim` on both
  backends. What remains is that such a plan is retried forever, taking its fair
  share of every poll cycle and reporting nothing to an operator beyond repeated
  `worker.tick_failed` log lines. Deliberate for now: a plan that fails from a
  transient cause must be free to recover, and nothing yet distinguishes that
  from a permanently poisoned one. A quarantine policy needs a consecutive-
  failure counter that a success resets, plus a surfaced block — neither exists.
- **Claim fairness has one-second granularity.** `claimed_at` is stamped
  `int(now.timestamp())`, so two claims inside the same wall-clock second tie and
  fall back to the `updated_at` tiebreak — which favours the plan that never
  saved, the exact bias the round-robin removes. The worker loop claims again
  immediately after a productive tick, so this is reachable. The effect is
  bounded (unfairness lasts until the second rolls over) rather than the previous
  unbounded starvation, and sub-second stamping would fix it if it ever matters.
  Not worth a change without evidence of multi-plan load — see the note below.
- **Multi-plan concurrency is lightly exercised.** Every fixture runs a single
  plan; parallel-goals-v1 runs two goals within one plan. The two entries above
  are the known multi-plan risks, both found by reading the claim path rather
  than by running it. Running many plans against one worker is not a near-term
  scenario, so these are recorded rather than engineered against.
- SSE is bounded and non-durable for clients; reconnect relies on refetch.
  Relay and event-table retention remain operational work.
- **A `request_concurrency` refusal still spends the per-task retry budget.**
  `capacity_wait` is set only when a circuit opens, and a concurrency refusal
  deliberately opens none, so alone among capacity failures it does not bypass
  the budget. Run evidence (2026-07-27, Tier 1): a task ended
  `execution_failure` after 7 attempts whose last was
  `rate_limit/request_concurrency` — a busy shared pool blocking a goal that has
  nothing wrong with it. The per-scope backoff curve fixed how long each wait
  is, not whether the attempt is charged. Making it budget-neutral inside a
  wall-clock bound is a capacity-policy decision, not a patch.
- **Never write a `PlanningArtifact` from inside an open plan transaction.** The
  store keeps its own short transaction so records survive a rollback, which
  means a second SQLite connection; WAL allows one writer, so the plan
  connection would hold the lock while waiting for the artifact connection and
  neither can proceed. `ExecutionHandler` queues and flushes after the
  transaction closes — new call sites must do the same.

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

## Control-plane exposure

Found by the Phase 3 capability audit (2026-07-28); both rows are classified in
[capability-matrix.md](capability-matrix.md) with an owner phase and the test
that closes them.

- **The token guard covers the catalogs, not the plan lifecycle.**
  `require_api_token` is declared on the `reference`, `config`, `reasoner`,
  `runner` and `metrics` routers; `plans.router` (`routers/plans.py:83`) and
  `events.router` (`routers/events.py:21`) declare no dependency. With
  `ORCHESTRATOR_API_TOKEN` set, 36 of the 64 served operations — every gate
  approval, `POST …/publication`, `DELETE /api/plans/{plan_id}`, and the whole
  plan document including brief and chat — still answer an unauthenticated
  caller, while `security.py:5` states that "every control-plane request must
  present it". Bounded in practice by the default `--host 127.0.0.1`, and the
  fixtures send the token on every call, so an operator reasonably believes it
  is enforced. `test_control_plane_token_guard` only covers `GET /api/providers`.
- **The settings forms silently clear provider and model capacity overrides.**
  `PUT /api/providers/{provider_id}` assigns `max_inflight` and `capacity_scope`
  unconditionally from the request body (`routers/reference.py:222`), and `PUT
  /api/models/{model_id}` rebuilds the row from `{name, max_inflight}` (`:266`).
  `ProvidersSection.tsx:283` sends neither field, so renaming a provider or
  model through the UI resets its in-flight ceiling to NULL — reverting an
  un-freeze #16 capacity decision with no warning and no event. Either the
  forms carry the fields or the update becomes partial.

## Invariants to preserve

1. No live aggregate reference crosses an agent/reasoner side effect.
2. Plan save, execution identity, and domain events share one UoW.
3. Test-author commits never reach the goal branch until independent GREEN
   verification accepts the implementation candidate.
4. Goal branches never reach the cycle branch until every task is DONE with
   accepted revision-bound evidence.
5. Resume changes availability only; targeted retry/block resolution are
   separate commands.
