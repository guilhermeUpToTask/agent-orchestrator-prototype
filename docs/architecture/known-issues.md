# Known issues and compatibility debt

Verified against the refactored code on 2026-07-14; re-verified 2026-07-20
against merged PRs through #29, with operator-workflow/documentation drift
re-verified 2026-07-25 and Phase 5 frontend debt re-audited 2026-08-01. Fixed
entries are removed; regressions live in tests rather than remaining as
warnings here.

The **Control-plane input validation** and **Contract repair from the UI**
entries below come from the Phase 4/5 code review of 2026-08-01. Every one was
reproduced against the real API or the real tail reader before being written
down; each entry carries the reproduction so a fix can turn it straight into a
regression test.

## Lifecycle compatibility

- `PlanPhase`, the legacy conversation/control routes, and root `goals`
  remain for readable migrated plans and existing clients. Cyclic plans route
  by `PlanStatus` plus open artifacts and active-cycle state, but deleting
  the compatibility surface requires a separately versioned API removal.

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

## Control-plane input validation

Phase 4's G6 fixed exactly one instance of this class (`max_attempts: 0` was
accepted and stored, so bounds moved onto the retry-policy DTO). The capacity
and binding DTOs Phase 5 started writing to were never given the same
treatment.

- **`max_inflight` has no bounds on providers or models, and both out-of-range
  values fail differently.** `ProviderCreateBody`/`ProviderUpdateBody`
  (`routers/reference.py:172-188`) and `ModelCreateBody` (`:243`) declare
  `max_inflight: int | None = None` with no `Field(ge=1)`. Reproduced
  2026-08-01 against `TestClient`: `POST /api/providers` and
  `POST /api/providers/{id}/models` return **201** for `0` and for negative
  values, and echo them back.
  - `0` is then **silently ignored**: `ExecutionHandler._provider_metadata`
    (`execution_handler.py:1556-1558`) resolves the cap with an `or` chain, so
    a falsy `0` falls through to the global `execution.provider_max_inflight`.
    Measured effective cap with provider *and* model set to `0`: **8**. The
    Settings screen renders the saved `0`, so the UI states a ceiling the
    scheduler does not apply.
  - A negative value **is** honoured, and wedges the plan: admission is
    `if inflight >= cap` (`execution_handler.py:1642`), so `0 >= -1` declines
    every attempt with nothing in flight. No circuit opens and no block opens —
    the plan simply waits forever, which is the hardest failure mode to
    diagnose from the outside.

  Fix is `Field(ge=1)` on all three bodies plus a rejection test per body; the
  `or` chain should become an explicit `is not None` check so a future stored
  `0` cannot re-acquire the silent-fallback meaning.

- **`capacity_scope` is an unvalidated free string.** The provider bodies
  declare `capacity_scope: str | None = None`. `POST /api/providers` with
  `"endpoint-wide"` (hyphen, not underscore) returns **201** and stores it
  verbatim; `resolve_capacity_scope` (`app/provider_capacity.py:166`) then
  degrades it to `PER_MODEL` at every read. The runtime tolerance is
  deliberate and correct — a typo must not take execution down — but nothing
  tells the operator their endpoint-wide provider is being scheduled per
  model. The write is where the value can still be refused; a
  `Literal["per_model", "endpoint_wide"]` on the DTO closes it without
  touching the tolerant reader.

- **An scp-style git remote cannot be bound, and the refusal blames a local
  path.** `validate_repo_url` (`infra/git/repository_binding.py:30-36`) routes
  on `urlparse().scheme`, and `git@github.com:acme/widgets.git` parses with an
  EMPTY scheme (`@` and `.` are not legal scheme characters), so the most
  common GitHub remote form is treated as a local filesystem path. Reproduced
  2026-08-01: `POST /api/projects` with that `repo_url` returns **422
  `PROJECT_BINDING_INVALID`**, `"repository path
  /workspaces/agent-orchestrator/backend/git@github.com:acme/widgets.git does
  not exist"`; the `https://` form of the same repository returns 201.
  `ProjectWorkspaceResolver.repository_path_for` (`:113`) makes the same
  assumption, so this is a genuine capability gap rather than validation
  drift — scp-style URLs were never clonable — but G11 turned a late failure
  into an early one that names the wrong cause. Either accept the form (detect
  `user@host:path` before falling through to a path) or say so: "scp-style
  remotes are not supported; use ssh:// or https://".

## Contract repair from the UI

- **`update_task_contract` from `DetailPanel` always re-authors the tests and
  always rebinds the agent.** The Phase 5 `ContractEditor`
  (`frontend/src/components/DetailPanel.tsx`) submits the complete contract on
  every save. Two backend paths key off field *presence*, not field *change*:
  - `edit_service.update_task_contract` calls `Task.semantic_edit` whenever
    `acceptance_criteria` or `objective` is present, which bumps
    `Task.revision` and invalidates revision-bound evidence and the test
    bundle. Reproduced 2026-08-01: the exact editor payload for a
    command-only fix leaves the task at **revision 2**, where
    `test_update_task_contract_repairs_a_frozen_contract_over_http` proves the
    equivalent command-only payload leaves it at **revision 1**. The cheap
    `amend_contract` path un-freeze #17 was built for is unreachable from the
    UI — "re-authoring a suite to fix a typo in a command" is exactly what it
    was meant to prevent.
  - `apply_edit` re-runs `match_agent` whenever `required_capabilities` is
    present (`app/use_cases/apply_edit.py:219-224`), so every repair rebinds
    the task's agent even when the capability list is unchanged. Reproduced
    2026-08-01: the editor payload returns **422 `NO_DEFAULT_AGENT`** on an
    install with no default agent, which only the rebind can raise; on a
    seeded install it silently replaces a deliberate `rebind_task_agent`
    choice.

  The fix belongs in the editor: send only the fields whose value differs from
  the loaded contract. No backend change is needed, and none should be made —
  the presence-based routing is what lets an API client ask for exactly one
  effect.

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
- **The attempt-log tail advertises a resume offset that can skip lines.**
  `follow_attempt_log` reads all complete lines available in one poll and then
  stamps EVERY resulting event with the batch-END offset
  (`infra/runtime/process_supervisor.py:132-134` — `_events_from_lines`
  receives the post-read `offset`, not a per-line one). The route serves that
  value as the SSE `id:` (`routers/plans.py:1513`), and the Phase 5 client
  records `id:` per frame and reconnects with `?offset=`
  (`frontend/src/lib/api.ts::subscribeToAttemptLog`). Reproduced 2026-08-01:
  three records written in one batch all carry offset `188` (the file size),
  and resuming from the offset advertised alongside frame 1 replays nothing —
  so a client that received only frame 1 before a network drop never sees
  frames 2 and 3. The window is a disconnect *between frames of one read*, so
  it is narrow, but the client's "neither duplicates nor drops output" is not
  true today. Fixing it means `_events_from_lines` computing a per-line
  cumulative offset; the client needs no change.
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

## Invariants to preserve

1. No live aggregate reference crosses an agent/reasoner side effect.
2. Plan save, execution identity, and domain events share one UoW.
3. Test-author commits never reach the goal branch until independent GREEN
   verification accepts the implementation candidate.
4. Goal branches never reach the cycle branch until every task is DONE with
   accepted revision-bound evidence.
5. Resume changes availability only; targeted retry/block resolution are
   separate commands.
