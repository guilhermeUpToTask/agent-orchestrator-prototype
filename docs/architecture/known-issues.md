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
failure behaviour, the missing contract-boundary control point, boot-time-only
reasoner config, `planning_artifacts` retention, and the P4.3 evidence-on-edit
finding — were moved here with their mechanisms and their options intact. **One
of them was stale on arrival:** the goal-promotion entry described code that had
already changed, because it was copied from the roadmap rather than re-verified.
It is corrected below, and re-reading the code before copying an entry is the
lesson. `ROADMAP.md` keeps only the scheduling decision and a pointer,
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
- **Goal promotion cannot recover from a cycle branch that MOVED.** *(Corrected
  2026-08-01: an earlier revision of this entry said promotion blocks on the
  first exception with no retry at all. That was carried over verbatim from the
  Phase 2 roadmap deferral and was already out of date — it describes code that
  has since changed. The retry exists.)*
  What is actually true: an ENVIRONMENTAL merge failure — a stale worktree
  registration, a held index lock — is classified by
  `is_transient_merge_failure` (`app/promotion_failures.py`, fail-closed: an
  unrecognized message is treated as permanent), the reservation is released,
  and the merge is re-attempted up to `MAX_PROMOTION_RETRIES` (2), counted in
  `planning_artifacts` so the loop is bounded
  (`ExecutionHandler._retry_promotion`). A verified goal is no longer thrown
  away because the repository was momentarily unusable.
  What remains open is the case a retry cannot fix: the cycle branch moved
  under the goal, so the same merge fails the same way every time and the block
  is correct. The repair is to rebase the goal branch onto the moved cycle
  branch and re-merge — deliberately **not** built, and Phase 8 work rather than
  a patch, because promotion runs no verification of its own
  (`_reserve_goal_promotion` only checks each task is DONE with accepted
  evidence, produced per task, on the task branch, against an OLDER base).
  Rebasing recombines verified code with code it was never tested against, so
  the repair must also introduce a goal-level re-verification step that has
  never existed. Skipping that would move unverified work upward, which is the
  one invariant the whole system rests on.
  Still the only block kind with **no observed run evidence**:
  `fixtures/parallel-goals-v1` exists to provoke it (two independent goals, one
  cycle branch, so the second merge runs against a base the first already moved)
  and passes — under dry-run each task writes its own artifact, so two goals
  never touch the same file. Provoking it needs Tier 1 with two goals over
  overlapping scope, and that evidence is what should decide whether the rebase
  is worth building.
- **Accepted evidence is deleted on edit, never retained as superseded.**
  `Task.semantic_edit` is the only path that bumps `Task.revision`, and it
  clears `verification_evidence` outright. A task carrying accepted evidence is
  also necessarily DONE (`accept_verification` and `complete_task` are atomic),
  and `_assert_task_mutable` refuses an edit on a DONE task — so the edit
  returns 409 and no revision bump ever occurs. `Plan.reopen_task` exists on the
  aggregate but nothing in `agent_orchestrator/app` calls it.
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

One entry from the same review remains recorded here rather than open:

- ~~**An scp-style git remote cannot be bound, and the refusal blames a local
  path.**~~ **Refused by name since 2026-08-01.** `git@github.com:acme/widgets.git`
  parses with an EMPTY scheme (`@` and `.` are not legal scheme characters), so
  it fell through to the local-path branch and was rejected as a missing
  directory — the wrong cause, for the most common remote form there is. The
  form is genuinely unsupported rather than merely unvalidated:
  `repository_path_for` makes the same scheme-based assumption and
  `_materialize_remote` skips a scheme-less URL, so no clone was ever
  attempted. Supporting it would mean changing the clone path and the
  workspace resolver; naming it costs one regex. `validate_repo_url` now
  refuses it with "scp-style git remotes are not supported … use the ssh://
  form … or https://", and the check is anchored so a real directory called
  `user@corp/repo` is still read as a path
  (`tests/integration/test_repository_binding.py`).

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

- ~~**Every `reasoner.*` config key is boot-time only, and the write that has
  no effect still reports success.**~~ **Fixed 2026-08-01.** `mode`,
  `provider_id`, `model_id`, `temperature` and `max_turns` took effect only
  after a worker restart: `PUT /api/config/orchestrator/reasoner.*` returned
  success, `GET /api/reasoner/status` reported the new value (the API process
  builds its own container), and the worker kept using what it booted with.
  Found while building `planning-recovery-v1`, whose whole mechanism is
  changing `reasoner.max_turns` mid-run — the change was accepted and silently
  ignored.
  `AppContainer.reasoner` now returns a `LiveReasoner`
  (`infra/reasoner/live_reasoner.py`) that re-resolves the configured reasoner
  on **every call**. Removing the `@cached_property` alone would NOT have fixed
  it — the stale reference is the one `PlanningHandler` captured at worker boot,
  not the one the property returns — which is why the fix lives at the call
  site, behind the port, where no handler has to learn about it. Per call is
  also the right granularity for safety: a planning call is a whole session, so
  a change lands between sessions and never swaps a model out mid-conversation.
  The cost is a config read and a key decrypt against an LLM round trip.
  A side effect worth knowing: an invalid configuration now surfaces at the
  planning call rather than at worker boot, so `_handle_reasoner_failure`
  records it against the plan where an operator can see it, instead of it being
  a startup traceback. Locked by `tests/unit/reasoner/test_live_reasoner.py`.

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
- ~~**The attempt-log tail advertises a resume offset that can skip lines.**~~
  **Fixed 2026-08-01.** `follow_attempt_log` read every complete line available
  in one poll and stamped them all with the batch-END offset, which the route
  serves as the SSE `id:` and the client records per frame — so a client that
  received frame 1 and then dropped resumed past frames 2..n. Reproduced with
  three records written in one batch, all carrying offset `188`. Each line now
  carries the byte offset that FOLLOWS it, counted off the raw bytes rather than
  decoded characters so non-ASCII output cannot desynchronize it
  (`tests/unit/runtime/test_attempt_log_tail.py`). The client needed no change,
  as the entry predicted.
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

## Shutdown

- **The worker has no graceful stop.** `orchestrate serve` reaps its worker on
  SIGINT/SIGTERM (fixed 2026-08-02, locked by
  `test_sigterm_to_serve_takes_the_worker_with_it` — before it, SIGTERM to the
  supervisor stopped the API and left the worker running against the same state
  directory, because uvicorn re-raises the captured signal and the reaping
  `finally` never ran). But the reap is `SIGTERM` to a process that installs no
  handler, so the worker dies where it stands rather than finishing its current
  atomic action. Nothing is corrupted — an interrupted attempt is exactly the
  crash case lease expiry and startup reconciliation are built for — so the cost
  is recovery latency: an orderly restart mid-attempt still waits out the goal
  lease (~5 min) like a hard kill. Making it graceful is Phase 2's deferred
  improvement (2), "release goal leases on graceful shutdown", and wants that
  design rather than a bare signal handler. `serve` still waits 30s before
  `SIGKILL`, which today is generosity toward a handler that does not exist yet.

## Operator walkthrough and documentation

- Reconciled 2026-08-02 for the package rename (`src/…` → `agent_orchestrator/…`
  in module docstrings and `overview.md`), `PROJECT_REPO_DIR` (removed from
  `overview.md`, `data-model.md` and the README's "going real" steps — the
  README told operators to export a variable nothing reads, which is the
  repository-binding trap the fixtures found), the state-directory layout,
  plan-level sequential execution (`execution-model.md` now describes per-goal
  leases and the in-process goal pool), startup reconciliation checking both
  leases, and the README architecture diagram's branch ladder.
- Still outstanding: the per-layer `README.md` files beside the code
  (`backend/agent_orchestrator/*/README.md`) and `backend/docs/INTEGRATION_GUIDE.md`
  have not been re-read end to end against the cyclic model; they are the most
  likely remaining home for pre-cyclic detail.

## Invariants to preserve

1. No live aggregate reference crosses an agent/reasoner side effect.
2. Plan save, execution identity, and domain events share one UoW.
3. Test-author commits never reach the goal branch until independent GREEN
   verification accepts the implementation candidate.
4. Goal branches never reach the cycle branch until every task is DONE with
   accepted revision-bound evidence.
5. Resume changes availability only; targeted retry/block resolution are
   separate commands.
