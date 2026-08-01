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

**This file is the single home for deferred defects.** On 2026-08-01 the
issue-shaped deferrals that had accumulated in `ROADMAP.md` — goal-promotion
first-failure behaviour, the missing contract-boundary control point,
boot-time-only reasoner config, `planning_artifacts` retention, and the P4.3
evidence-on-edit finding — were moved here with their mechanisms and their
options intact. `ROADMAP.md` keeps only the scheduling decision and a pointer,
per its own rule that verified unresolved defects are not duplicated there.
What did NOT move: work that is a missing *feature* rather than a defect
(forge publication, `ProjectSpec`, sandboxing, an operator skip/abandon
command) stays in Phase 8, where it is scheduled against preview evidence.

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
- **Goal promotion fails closed on the first exception, including a transient
  one.** `goal_promotion_failure` opens on the FIRST exception out of
  `merge_goal` — a transient git or filesystem error included — with no retry,
  and advertises exactly one resolution, `start_replan`: the whole-cycle hammer
  for what may be a momentary lock. It is the only block kind with **no
  observed run evidence**; `fixtures/parallel-goals-v1` exists to provoke it
  (two independent goals, one cycle branch, so the second merge runs against a
  base the first already moved) and passes — under dry-run each task writes its
  own artifact, so two goals never touch the same file. A genuine conflict
  needs Tier 1 with two goals over overlapping scope.
  The smallest honest fix is to retry a merge that failed for a transient
  reason before blocking, and to stop advertising `start_replan` as the only
  way out of a git error a retry would clear. The *full* repair — rebase the
  goal branch onto the moved cycle branch and re-merge — is deliberately not
  built, and is Phase 8 work rather than a patch: promotion runs no
  verification of its own (`_reserve_goal_promotion` only checks each task is
  DONE with accepted evidence, produced per task, on the task branch, against
  an OLDER base), so rebasing recombines verified code with code it was never
  tested against and would need a goal-level re-verification step that has
  never existed. Skipping that would move unverified work upward.
- **Accepted evidence is deleted on edit, never retained as superseded.**
  `Task.semantic_edit` is the only path that bumps `Task.revision`, and it
  clears `verification_evidence` outright. A task carrying accepted evidence is
  also necessarily DONE (`accept_verification` and `complete_task` are atomic),
  and `_assert_task_mutable` refuses an edit on a DONE task — so the edit
  returns 409 and no revision bump ever occurs. `Plan.reopen_task` exists on the
  aggregate but nothing in `src/app` calls it.
  Consequence: the evidence read model's `superseded_evidence_count` and its
  `task_revision == task.revision` filter are unreachable today. **Both are
  kept deliberately** — insurance on the endpoint's central claim. If the
  domain is ever changed to retain evidence across a revision bump, that filter
  is what stops the endpoint serving stale evidence as accepted on day one. Do
  not delete either as dead code without reading this entry. Whether superseded
  evidence *should* be retained-and-marked rather than deleted is a
  frozen-domain question needing a decision-log entry and an un-freeze.
- Execution attempts have global UUIDs and monotonic absolute numbers, but the
  execution ledger does not yet promote `run_kind` as a dedicated SQL column.
  Role identity is present in the orchestration path and separate invocations,
  prompts/specs, worktrees, run ids, and evidence.

## Control-plane input validation

Phase 4's G6 fixed exactly one instance of this class (`max_attempts: 0` was
accepted and stored, so bounds moved onto the retry-policy DTO). The capacity
DTOs Phase 5 started writing to were given the same treatment on 2026-08-01 —
`max_inflight` is `Field(ge=1)` on all three bodies and `capacity_scope` is a
`Literal`, locked by the `capacity`/`max_inflight` tests in
`tests/integration/test_api.py`. The read side no longer trusts either door:
`resolve_max_inflight` (`app/provider_capacity.py`) skips a non-positive
candidate wherever it comes from — a row written before the bound, or
`execution.provider_max_inflight`, whose stored `"0"` is a truthy STRING the
factory's `or` never caught — so the worst an unusable value can now do is fall
back to a working one
(`tests/unit/test_provider_capacity.py`,
`tests/unit/test_provider_capacity_factory.py`, and
`test_an_unusable_stored_cap_does_not_wedge_the_plan` on both backends).

What remains open in this section:

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

- ~~**`update_task_contract` from `DetailPanel` always re-authors the tests and
  always rebinds the agent.**~~ **Fixed 2026-08-01.** The editor submitted the
  complete contract on every save, and two backend paths key off field
  *presence*, not field *change*: `objective`/`acceptance_criteria` send the
  task through `Task.semantic_edit` (revision bump, revision-bound evidence and
  test bundle invalidated), and `required_capabilities` makes `apply_edit`
  re-run `match_agent`. So a one-command repair cost a re-authoring plus an
  agent rebind — the exact price un-freeze #17 added `amend_contract` to avoid.
  The editor now diffs against the contract as loaded and submits only the
  changed fields (`frontend/src/lib/contractEdit.ts`, locked by
  `contractEdit.test.ts`); a save with nothing changed submits nothing. The
  backend is deliberately unchanged — presence-based routing is what lets an
  API client ask for exactly one effect, and sending the delta is how a form
  asks for exactly one effect.

## Configuration staleness

- **Every `reasoner.*` config key is boot-time only, and the write that has no
  effect still reports success.** `AppContainer.reasoner` is a
  `@cached_property` and the worker resolves it once at startup, so `mode`,
  `provider_id`, `model_id`, `temperature` and `max_turns` take effect only
  after a worker restart. `PUT /api/config/orchestrator/...` returns success,
  `GET /api/reasoner/status` reports the new value (the API process builds its
  own container), and the worker keeps using what it booted with. Found while
  building `planning-recovery-v1`, whose whole mechanism is changing
  `reasoner.max_turns` mid-run: the change was accepted and silently ignored.
  The caching is deliberate — rebuilding per tick would re-read the secret
  store and re-resolve the catalog on every poll — so this is a **staleness
  decision, not a bug to patch blindly**. The options, smallest last: a
  generation counter on the config table the worker compares each tick and
  invalidates on change; invalidation scoped to the keys that are safe to swap
  mid-flight (a model swap between attempts is fine, a mode swap mid-session is
  not); or leave it boot-time and say so in `GET /api/reasoner/status` and the
  config write response, so an operator is told a restart is required instead
  of watching a successful write do nothing. The last is the smallest honest
  fix and probably the right first move.
  The same question applies to `agent_runner.*`, which resolves per task per
  run and is therefore probably already live — verify rather than assume.

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
- **Nothing can hold a plan at the contract boundary.** A worker enriches a
  goal and executes its first task under ONE claim, and the pause gate blocks
  *claims* — so arming a pause before enrichment stops enrichment too, and
  arming it afterwards is unwinnable: there is no window. A plan sitting at a
  review gate is `waiting` and cannot be paused at all. Net effect: nothing
  outside the process can hold a plan between "the contract is frozen" and "an
  agent is running against it". Found while building a live fixture for
  contract repair (un-freeze #17), which needs exactly that boundary; priced
  concretely by `contract-repair-v1`, which must win a race for the window and
  so is a paid Tier 1 test that succeeds about half the time instead of a free
  deterministic one. The behaviour is arguably correct — the JIT loop exists to
  avoid planning work nobody asked for — but a whole class of contract-level
  operator scenario can then only be tested by stopping the worker, which is
  outside an API-only walkthrough. Those scenarios are covered at the
  orchestration level instead
  (`tests/unit/orchestration/test_contract_editing.py` drives both handlers a
  step at a time, on both backends). If it is picked up, the smallest useful
  lever is a review-gate-like hold at the contract boundary, opt-in per plan so
  the JIT loop stays the default — not a general "pause between units", which
  would turn every unit boundary into a scheduling decision.
- **Never write a `PlanningArtifact` from inside an open plan transaction.** The
  store keeps its own short transaction so records survive a rollback, which
  means a second SQLite connection; WAL allows one writer, so the plan
  connection would hold the lock while waiting for the artifact connection and
  neither can proceed. `ExecutionHandler` queues and flushes after the
  transaction closes — new call sites must do the same.

## Retention and cleanup

- Owned process groups are terminated and reaped on success, failure, timeout,
  discard, and stale results. Worker startup now prunes and audits worktrees, but
  a host crash can leave metadata until the next worker startup; there is no
  operator-triggered branch/checkpoint garbage-collection policy.
- Authoritative test checkpoint branch refs are retained after implementation
  forks from their immutable commit. They preserve auditability but need a
  retention policy for long-running repositories.
- **`planning_artifacts` grows without bound, deliberately.** Migration 0014
  keeps what a failed planning attempt produced so the retry starts better
  informed, and nothing prunes it. That is an accepted deferral, not an
  oversight: reads are already bounded — keyed by `(plan_id, purpose, goal_id)`
  with `limit=5`, served by `ix_planning_artifacts_lookup` — so growth costs
  storage, never read latency or correctness; a replan mints new goal ids
  (`activate_cycle`), so a superseded cycle's rows become permanently
  *unreachable* rather than wrong; and `ON DELETE CASCADE` from `plans` already
  collects them when a plan is deleted. A long-lived plan through several
  replans therefore accumulates rows nothing will ever read again.
  What a retention sweep would have to decide: last N attempts per
  `(plan, purpose, goal)` versus an age bound; whether `committed` outcomes are
  worth keeping once the contract they produced is frozen; and whether pruning
  belongs in the store's `append` (cheap, amortised) or a maintenance command
  (visible, operator-triggered). **Do not add a background sweeper** — this
  codebase has no scheduler, and inventing one for garbage collection is
  exactly the coordination infrastructure the roadmap forbids without run
  evidence.

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
