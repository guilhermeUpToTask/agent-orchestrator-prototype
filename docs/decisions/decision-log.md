# Decision log

*Every locked design decision, consolidated. Decisions 1-42 remain historical
evidence, but incompatible lifecycle statements are superseded by decision 43
and [ADR-003](adr-003-cyclic-project-plan-lifecycle.md).*

## Historical phase machine (superseded by decision 43)

1. **Nine phases**: DISCOVERY, REPLANNING, ARCHITECTURE, ENRICHING, AWAITING_REVIEW, RUNNING, REVIEW, DONE, FAILED. Terminal = {DONE, FAILED}.
2. **ENRICHING is separate from ARCHITECTURE** for crash-recovery granularity — more phase boundaries = more checkpoints = finer resume. Semantically one activity in two steps, deliberately.
3. **REPLANNING = conversational re-plan** ("refining" means conversation, *not* manual editing). Two entry points — REVIEW "replan" and mid-RUNNING chat — one phase.
4. **`apply_edit` ≠ `request_replan`**: surgical manual edit vs holistic conversational re-plan; separate use cases, never conflated.
5. **The loop is append-only**: new iterations' goals are appended; prior DONE goals are untouchable history *and* re-plan context. The `iteration` counter increments at exactly one point — when REPLANNING commits its goal set.
6. **Mid-RUNNING replan abandons in-flight work**: PENDING goals/tasks are SKIPPED at request time; the in-flight task finalizes tolerantly; commit-time finalize-abandon closes the rest (the "resurrection bug" fix — a stale goal must never re-execute after the next iteration starts).
7. **Gates ALWAYS pause.** `pause_after` was removed — checkpoints are now phases. (Fixed the verified gate-spin bug.)
8. **RUNNING exhausts into REVIEW, never DONE.** DONE is reached only via REVIEW "finish" (which emits `PlanCompleted`).
9. **The driver model**: conversational phases are chat/API-driven, ARCHITECTURE/ENRICHING/RUNNING are worker-driven, gates are human-command-driven. The claim predicate = the worker-driven set — non-worker phases are *invisible* to workers, so they can never churn.

## Concurrency, lease, recovery (ADR-001, locked 2026-07-02)

10. **Per-plan lease now; sequential per plan.** The lease *granularity* is the unit of parallelism — moving it to goal/task level is the designed future switch. When parallelism is needed: move the lease, don't bolt a queue on top.
11. **The lease replaces continuous domain reconciliation.** Expired lease = reclaimable; a dead worker needs no supervisor mutation of task outcomes. Decision 45 adds startup-only operational-ledger reconciliation without changing that authority.
12. **The worker tick reports *progress*, not *claiming*** — a claim yielding only `not_ready`/`paused` sleeps instead of spinning (fixed the verified worker-tick spin bug).
13. **Historical: heartbeats between units only.** Superseded by decision 45: active atomic actions now renew the lease at one-third of its interval.
14. **Scheduler = thin, idempotent, OS-supervised** (when it lands); no distributed consensus. Lease = recovery *mechanism*; health = *visibility* (a separate, still-unbuilt surface).

## Retry & failure (locked 2026-07-02)

15. **The domain decides, the adapter waits.** `should_retry`/`backoff_for` are domain rules; backoff is the durable `retry_not_before` timestamp the scan honors — never a domain sleep, survives crashes.
16. **Shared failure taxonomy** (`FailureKind`): `connection_error | rate_limit | timeout | tool_error` retryable; `token_limit | auth_error` terminal. Produced by the real CLI runners **and** the dry-run dummy, so dry-run exercises the production retry paths. Classification is conservative: unknown ⇒ retryable `TOOL_ERROR`.
17. **Manual retry** — *built* as `Plan.resume()` (un-freeze #3, decision 42): clear the pause gate, reset attempts, requeue every FAILED task in a non-terminal goal, bypassing `should_retry`.
18. **A failed goal halts the plan** (safe default); skip-and-continue is a future knob. *Amended by un-freeze #3 (decision 42)*: the halt is now a recoverable **auto-pause** (task stays FAILED, goal stays open, plan stays RUNNING+paused) rather than terminal FAILED; `fail_goal` was deleted. Terminal FAILED is reachable only via `fail_plan` (permanent reasoner failure).

## Mutation safety (locked 2026-07-02)

19. **No global stop / SystemBusyError.** Safety is the layered guards: version-CAS on every save, status guards on task/goal edits, delete-guards on referenced reference data. Plan-DELETE will check the lease (`PLAN_BUSY`), plan-*edits* rely on the goal/task guards (editing a pending goal while a sibling runs is allowed).
20. **Rebind-on-edit**: manually setting `agent_id` = explicit override (no auto-rematch); editing `required_capabilities` = re-run `match_agent`.
21. **Optimistic concurrency everywhere**: `bump_version()` before `save()`; the store rejects stale writes (`StaleVersionError` → 409). The worker-vs-human race is surfaced, never silently lost.

## Reasoner & planning content (locked 2026-07-03)

22. **The Reasoner port is exactly two methods** — `converse(plan, history, message, mode)` and `enrich_goal(plan, goal, capabilities)`. The four phase-specific methods (`draft_goals`/`structure_goals`/`enrich_goals`/`replan_goals`) were deleted.
23. **Multi-turn with commit**: a reply without goals keeps the conversation open; a reply with goals *is* the roadmap commit. The user message persists before the LLM call.
24. **ARCHITECTURE is a no-LLM passthrough** — evaluated, not accidental: the conversation already commits the user-agreed roadmap; an autonomous re-structuring pass would be redundant and risks mangling what the user signed off. The handler is the seam if a real pass returns.
25. **ENRICHING = JIT task population only** — one task-less goal per step, 1..N plain tasks, idempotent, checkpointed. No TDD test-writer/implementer pairing in this prototype.
26. **Handlers re-validate all tool arguments** (never trust provider schema enforcement); history replays as plain text (never provider transcripts — immune to dangling tool calls and provider switches); `{accepted:false, errors}` drives self-correction.
27. **Chat replies travel in the HTTP response body**; SSE carries only domain events. Chat persistence is its own short transactions — display history and plan truth can never roll each other back.

## Runtime & catalog resolution (locked 2026-07-03 → 05)

28. **No `AGENT_MODE` env var.** Runtime selection is data in SQLite: `reasoner.mode` and `agent_runner.mode` config keys. Environment is read only in the composition root.
29. **Credentials resolve through the providers catalog** — provider row (`base_url` + envelope-encrypted `api_key_ref`) + model row; the secret store is a thunk so stub/dry-run never construct it (no master key needed until real mode).
30. **🔓 Domain un-freeze #1 (2026-07-05)**: `AgentSpec.runtime_type/provider_id/model_id` added — the agent registry owns runtime resolution. Per-task, per-run resolution (no cached runners) so agent edits and key rotations apply without restarts; a broken binding is terminal `AUTH_ERROR`.

## Infrastructure & data (locked 2026-07-02)

31. **Plan = one JSON document** in SQLite; promoted columns only for SQL predicates. Fresh parse per `get()` = detached aggregates = fake and real adapters behave identically.
32. **SQLite WAL + `synchronous=FULL` + `foreign_keys=ON` + busy_timeout**, applied per-connection.
33. **Transactional outbox** + a poller relay in the API process → SSE; at-least-once, publish-then-mark, consumers dedup on `event_id`. Telemetry unifies on outbox + agent_events — **no second event system**.
34. **Git-branching workspace = the rollback**: worktree per attempt, `--no-ff` merge on success, branch-delete on failure; `main` never touched. Zip/local-dir outputs and GitHub PR are output strategies behind the Workspace port (PR deferred).
35. **Old data thrown away at the refactor** (it was test data) — clean break, no migration. Integration rollback was user-handled git branches.
36. **Secrets reuse envelope encryption; never logged.** One decryption point.

## Testing & process (locked 2026-07-02)

37. **The truth test is the keystone**: the orchestration suite runs against in-memory fakes AND real SQLite via one parametrized fixture; crash-recovery/outbox-rollback/backoff-survives-crash passing on real SQLite is the proof atomicity is real. Keep fake and real semantics identical.
38. **The dummy runner must imitate a real agent** (taxonomy-matched failures, realistic behavior) and has its own tests — a weak dummy makes every dry-run e2e meaningless.
39. **Paid real-model tests never run per-PR** — cost-gated behind `pytest -m llm` + env keys; CI (when it lands) splits per-PR (unit+integration+dummy-e2e+lint) from nightly (paid smoke).
40. **🔒 The domain freeze itself** (2026-07-02): no core changes without a deliberate, recorded un-freeze. Mid-integration core churn was judged the project's top risk — this is the mitigation.
41. **🔓 Domain un-freeze #2 (2026-07-08)**: `Plan.planning_retry_not_before` + `planning_attempts` fields and the `record_planning_retry` / `clear_planning_retry` / `fail_plan` transitions. A transient reasoner failure in a worker-driven planning phase (ARCHITECTURE/ENRICHING) now arms a **durable plan-level backoff gate** — the planning-phase analog of a Task's `retry_not_before` + attempt (decision 15) — honored by the claim predicate, so a rate-limited provider makes the worker back off instead of hot-looping it. The budget reuses `retry_policy` (`backoff_for`/`max_attempts`); exhausting it, or a permanent failure, transitions the plan to FAILED via `fail_plan`. Motivated by a live OpenRouter rate-limit (`ResourceExhausted 502`) that produced a ~1s `worker.tick_failed` storm with no operator-visible signal.

42. **🔓 Domain un-freeze #3 (2026-07-09)**: the **pause gate, recoverable failure, editable-while-paused, and strict in-goal order**, motivated by a real run that died terminally on a free-tier daily rate limit with no way to intervene, retry, or edit. Enumerated changes:
    - **Pause gate (not a phase).** New `Plan.paused: bool` + `Plan.paused_reason: str | None`, with `pause(reason)` (guarded to `WORKER_CLAIMABLE_PHASES`, idempotent) and `resume() -> list[str]`. `paused` is a promoted column (`plans.paused`) ANDed into the claim predicate — the same availability-flag pattern un-freeze #2 established with `retry_not_before`, orthogonal to lifecycle position. The nine-phase enum is untouched. This **bends decision 7** ("gates always pause / checkpoints are phases"): gates remain the only *phase-level* pauses; the pause gate is a second, operator-driven pause that is not a phase.
    - **Resume = the manual retry** (implements decision 17). `resume()` clears the pause gate and the planning backoff gate, and returns every FAILED task in a non-terminal goal to PENDING with a fresh attempt budget (new `Task.retry()`: FAILED→PENDING, `attempt=0`, `result=None`, gate cleared — bypassing `should_retry` by construction), plus `Task.clear_backoff()` on backing-off PENDING siblings. Decision 17's "clear the gate, reset attempts, requeue, bypassing should_retry" is now built, folded into one human verb rather than a standalone endpoint.
    - **Auto-pause replaces terminal goal failure** (**amends decision 18**). A terminal task failure (retry budget exhausted, or a non-retryable `auth_error`/`token_limit`) now `fail_task` + `pause(reason)` in the same finalize transaction and emits `PlanPaused(auto=True)` instead of failing the plan. `Plan.fail_goal()` is **deleted**; `GoalFailedEvent` is no longer emitted from execution. The halt is preserved (nothing runs until a human acts) but recovery is in-band. Terminal FAILED is now reachable **only** via `fail_plan` (permanent reasoner failure in a planning phase). Skip-and-continue remains a future knob.
    - **Reopen-discovery.** New guarded `reopen_discovery()` (`AWAITING_REVIEW → DISCOVERY`, clears pause) for "request changes" at the pre-execution gate. The next commit flows through `set_iteration_goals`, which **replaces** the un-executed roadmap (terminal history kept) — distinct from REPLANNING, whose commit appends and bumps `iteration`.
    - **Strict in-goal order.** `next_action` now blocks a goal on its head (first non-terminal, position order) task: a backing-off head yields the whole goal instead of skipping ahead to a later task. Tasks in a goal are a sequential chain; cross-goal order is unchanged (position + `depends_on`). `begin_replanning()` also clears the pause gate so a committed re-plan can execute.
    - **New edit ops + paused-aware guards.** `edit_service` gains `update_task`, `update_goal` (name/description/`depends_on` with existence + acyclicity validation), and `remove_goal` (strips dangling `depends_on`, renumbers). All edit ops take `paused`: a RUNNING goal is editable only while paused, a FAILED task is editable/removable/rebindable only while paused, a RUNNING task never.
    - **Events + telemetry.** New outbox events `PlanPaused{reason, auto}` / `PlanResumed{retried_task_ids}`; `kind` (FailureKind) added to `TaskRequeued` / `TaskFailedEvent`. `AgentEvent.task_id` becomes nullable so the reasoner can emit plan-scoped `llm.call` token-usage rows on the existing `agent_events` stream (**honors decision 33** — no third event system; the only schema bend is the nullable `task_id`). New read-side `agent_event_reader` (per-task history + a metrics roll-up via `json_extract`), `GET /plans/{id}/agent-events`, and `GET /api/metrics`.
    - Migration `0006_pause_and_telemetry` (chained after 0005): `plans.paused`, nullable `agent_events.task_id`, `ix_agent_events_plan_task`.

## Deferred by decision (seams preserved)

PR gate · project spec governance · decision gate · ~~GitHub PR output~~ (built 2026-08-02, decision 61) · parallelism · env provisioner · Postgres · Redis claim path · pi NDJSON streaming. Details and reintroduction designs: [../legacy/pre-refactor-backend.md](../legacy/pre-refactor-backend.md); scheduling: [ROADMAP.md](../../ROADMAP.md).


43. **Domain unfreeze #4 (2026-07-14): cyclic ProjectPlan + deterministic TDD execution.** [ADR-003](adr-003-cyclic-project-plan-lifecycle.md) deliberately supersedes the terminal nine-phase lifecycle and the incompatible parts of decisions 1-9, 13, 17-18, 22, 24-25, 34-35, and unfreezes 2-3. One immutable project owns one long-lived plan; root status is `running | paused | waiting | blocked | idle`; finite work lives in cycles; intent, architecture, and publication are exact-revision review gates; pause and retry are separate; runs are monotonic and leased; task completion requires protected, independently verified executable evidence; and verified task-to-goal-to-cycle staging produces one recorded output disposition per cycle. Legacy rows are preserved through the explicit mapping and project-binding quarantine in ADR-003; ownership and approval/publication history are never fabricated.

44. **Domain unfreeze #5 (2026-07-14): durable Git-promotion reservation.** Candidate and goal promotion now reserve the plan before crossing the database-to-Git side-effect boundary. While the reservation is held, pause requests remain legal but replans, semantic edits, intent/draft replacement, and cycle activation are rejected. Finalization re-reads the reservation and captured cycle/task identity before clearing it in the same transaction as task/goal completion. This closes the check-to-merge race without holding a database transaction open across Git I/O.

45. **Operational recovery, provider circuits, and truthful timelines (2026-07-15).** Planning LLM calls are durable `PlanningOperation`s; task invocations persist normalized runtime/provider/model failure evidence and bounded redacted output. Retry defaults are jittered 30s→15m, provider `Retry-After` is a floor, and a persisted runtime/provider/model circuit escalates repeated capacity failures into a structured block with explicit recovery actions. Worker startup abandons stale RUNNING attempt/run rows only when the plan has no live claim, leaving domain task state to the lease-driven reclaim choreography, then conservatively prunes/audits git worktrees. The attempt-history API hydrates the console before SSE; metrics report planner/child/combined coverage with unavailable distinct from zero. This amends decisions 11, 13, 16, 33, and the observability portion of 42 without reintroducing a continuous domain reconciler.


46. **Domain unfreeze #6 (2026-07-16): executable recovery and source-preserving replan review.** Structured block actions are now commands rather than display-only strings: execution blocks target one failed task; provider-capacity retry clears only the evidence-linked runtime/provider/model circuit; reasoner blocks retry their current planning stage; and `edit_task` permits a semantic correction of only the blocked target before resolving that block. Resume remains availability-only, absolute attempt identity and unrelated gates/tasks are preserved, and block resolution plus aggregate state plus outbox event commit in one UoW/CAS transaction. Plan detail now exports typed cycles, proposal, draft, gate, and block artifacts. The console renders retry/edit controls, explicit replan intent, locked source-cycle history, and a side-by-side editable CycleDraft. Replan reasoner context includes source-cycle results and unfinished work and explicitly forbids recreating DONE work. No root status or phase transition was added; this deliberately unfreezes only recovery guards, recovery commands, and replan context/review presentation under ADR-003.

47. **Domain unfreeze #7 (2026-07-16): live-registry recovery for frozen task contracts.** An `agent_capability` block now advertises `retry_stage`. Recovery snapshots the blocked goal's frozen task requirements, resolves every mandatory TDD role from the user-managed agent registry outside the plan transaction, then re-reads and version-checks the aggregate before applying all bindings, resolving the block, bumping version, and writing `BlockResolved` in one UoW. Partial binding is forbidden: any uncovered role leaves the plan and every task unchanged. Explicit role matches no longer depend on a default agent. The demo seed provides the mandatory role-capability vocabulary as bootstrap data only; registry-defined execution profiles and preflight coverage remain roadmap work.

48. **Domain unfreeze #8 (2026-07-17): runnable bootstrap and strict cyclic
recovery/routing invariants.** The default dry-run runtime now produces
deterministic role-specific artifacts so the shipped stub + seeded
`test_authoring`/`implementation` agent traverses the same Git, frozen-test,
scope, verification-evidence, and publication choreography as a real agent.
Repository-root scope is normalized explicitly; deleted test artifacts become
recoverable verification failures. CycleDraft creation and later dependency
edits reject edges to same-position or later goals, preserving the positional
scheduling barrier. Migrated project-less plans gain the advertised
transactional `project-binding` command. Project workspaces detect each
repository's actual default branch and cache by current repository identity, so
configuration changes cannot strand work on `main` assumptions or a stale
repository. The unimplemented active `cancel_cycle` advertisement is removed;
cycle cancellation remains an explicit publication disposition or draft
cancellation, never a display-only command.

49. **Domain unfreeze #9 (2026-07-20): cyclic-aware pause & replan guards.**
`Plan.request_pause`, `Plan.pause`, and `Plan.begin_replanning` guarded on the
legacy `PlanPhase` (`WORKER_CLAIMABLE_PHASES` / `{RUNNING, REVIEW}`), but a
cyclic plan's advertised `legal_actions` derive from root `PlanStatus` + open
artifacts. A running or blocked cyclic plan whose legacy phase projection is
`REPLANNING` therefore advertised `pause` / `start_replan` while the domain
transition rejected them (`INVALID_TRANSITION`) — wedging plans with no working
recovery, surfaced by the 2026-07-20 API walkthrough (findings #2/#5). The
guards now consult the cyclic authority exactly as `resume()` already did: pause
is legal when `status == RUNNING` and (`active_cycle is not None` or the legacy
phase is claimable); `begin_replanning` skips the legacy phase guard when
`active_cycle is not None`. Legacy (pre-cyclic) plans keep the phase guards
unchanged. No new fields or statuses; this aligns advertised `legal_actions`
with the transition guards so every advertised recovery is executable.

50. **Domain unfreeze #10 (2026-07-21): coherent cyclic conversational-replan
state.** Findings #12/#13: `request_replan` composed `begin_replanning` (which
sets `phase=REPLANNING` → `status=WAITING`) then `resolve_block("start_replan")`
(whose generic fallback set `status=PAUSED` without `paused=True`), leaving the
invalid tuple `status=PAUSED, paused=False`, and never retired the stale
`intent_proposal`/`cycle_draft`/`review_gate`. So `legal_actions` advertised
`resume` (which `resume()` then rejected on the `paused` field), the worker
couldn't claim the plan (`status≠running`), and an approved source intent
masqueraded as active planning work. Fix: (a) `begin_replanning` on a cyclic plan
(active cycle present) now establishes the explicit WAITING replan tuple —
clears the current intent/draft/gate slots and `pause_requested`, retains the
source `Cycle` (legacy `active_cycle is None` plans byte-identical); (b)
`request_replan` resolves the block BEFORE `begin_replanning` so the WAITING
status is the transaction's final, atomic lifecycle word; (c) `legal_actions`
advertises `resume` only when the `paused` flag is truly armed, and `start_intent`
only for INITIAL planning (no active cycle); (d) `activity` reports
`replan_discovery` for the tuple. Live-verified on a wedged v77 plan.
`resolve_block`'s generic status fallback is left unchanged (its only caller is
`request_replan`) and recorded as compatibility debt. Builds on unfreeze #9.
A codex `gpt-5.6-sol` design analysis (with a 14-item legacy-`PlanPhase`
side-effect audit) informed this scope; the audit items are tracked follow-ups,
not part of this narrow unfreeze.

51. **Domain unfreeze #11 (2026-07-21): SKIPPED is legacy-only; cyclic
navigation and promotion share one predicate.** Finding #3 and the maintainer's
question ("does a skippable goal/task make sense?"). `SKIPPED` is legacy
append-only iteration-abandonment residue; in the cyclic model the abandonment
boundary is cycle SUPERSEDED-on-activation, not per-task skipping. Yet
`request_replan` skipped every pending/running/failed task in the **still-active
source cycle**, and `begin_replanning`'s root-goal skip loop ran for cyclic plans
too — creating goals with `SKIPPED` tasks that navigation treats as closeable
(`{DONE, SKIPPED}`) but promotion rejects (needs every task `DONE`-with-evidence),
i.e. permanently unpromotable (the root of #3/#4/#12). Fix: (a) `request_replan`
no longer rewrites the source cycle's task outcomes; (b) `begin_replanning` skips
nothing on the cyclic branch (legacy branch byte-identical) and sets the WAITING
tuple with `phase`/`status` written explicitly rather than via `_set_phase`'s dual
authority (partial issue #41 cleanup); (c) a canonical `can_promote_goal()`
predicate (`navigation.py`) — every task `DONE` with accepted evidence — is now
the single rule `_reserve_goal_promotion` uses, so navigation and promotion agree;
(d) `conversation._start_operation` allows replan discovery for a plan already in
the WAITING/REPLANNING conversational replan (the frozen source cycle need not be
settled — it is superseded on activation). `Task.skip()`/`Goal.skip()`/
`Status.SKIPPED` remain for legacy plans and history; no migration. A
maintainer-directed codex `gpt-5.6-sol` analysis chose this direction. Composes
with unfreezes #9/#10. Full navigation `GOAL_UNPROMOTABLE` typing and cyclic
stale-result ledger settlement remain scoped follow-ups.

52. **Domain unfreeze #12 (2026-07-22): operator-tunable retry policy on an
already-persisted plan.** `Plan.retry_policy` was captured once at creation
(`RetryPolicy()` bare defaults: `max_attempts=3`, `max_backoff_seconds=900`)
and never revisited, so a plan stuck on a persistent transient failure (a
rate-limited provider observed during a live walkthrough, `provider_capacity`
blocks recurring for ~38 minutes across repeated manual `wait_and_retry`
resolutions) had no way to widen its backoff budget short of a replan — the
new `execution.retry_*` config keys (`agent_orchestrator/infra/policies/retry_policy_factory.py`)
only seed a plan's policy AT CREATION and are deliberately never consulted
again for an existing plan (config is a live global; a plan's policy is
persisted, per-plan state — conflating the two would let a global config edit
retroactively reinterpret an attempt count or backoff already computed under
the old policy for an in-flight task). Fix: a new guarded mutation
`Plan.update_retry_policy(retry_policy)` (`planner_orchestrator.py`) — legal at
any status (including blocked/paused; only a legacy-terminal plan rejects it,
same `_assert_not_terminal()` guard as the planning-retry gate), touching only
the plan's own `retry_policy` field and never any in-flight task's attempt
count or armed `retry_not_before`. `POST /api/plans/{id}/retry-policy` accepts
a partial body (only the fields an operator sets are changed) and merges over
the plan's current policy in the same transaction as the version bump — same
"editorial mutation, no outbox event" shape as `apply_edit`. Composes with
unfreeze #11's `can_promote_goal()` cleanup; touches no goal/task/navigation
state.

53. **Domain unfreeze #13 (2026-07-22): goal-level parallelism domain
primitives — shared dependency graph, additive readiness, per-goal
promotion reservation.** [ADR-001](adr-001-concurrency-lease.md) (decision
10, 2026-07-02) designed the lease granularity as the deliberate future
parallelism switch (plan → goal → task) without yet implementing it. This
unfreeze does the domain-layer third: (a) `CycleDraft.validate_dependencies`'s
inline cycle-detection DFS is lifted into a shared, pure
`domain/services/dependency_graph.py` (`validate_acyclic` + a new
`ready_nodes` primitive) so goal-parallelism scheduling and cycle-draft
validation share one DAG implementation instead of two independently
maintained traversals; (b) `navigation.py` gains **additive** functions
`ready_goal_ids` (which non-terminal goals have every `depends_on`
dependency DONE — the ADR's "next_action must return a set" requirement,
satisfied without changing `next_action`'s own signature so every existing
caller/test and all legacy-plan behavior stay byte-identical) and
`action_for_goal` (the per-goal tail `next_action` already had, now shared
rather than duplicated for goal-selected dispatch); (c) `Plan.promotion_reservation`
(a single plan-wide `str | None` scalar guarding BOTH task-attempt-in-flight
and goal-merge-in-flight, whichever needed it at the time) becomes
`goal_promotion_reservations: dict[str, str]` keyed by `goal_id`, so two
different goals' task attempts/promotions no longer contend on the same
slot — the actual blocker to running them concurrently. `reserve_promotion`/
`release_promotion` take `goal_id` explicitly; `assert_lifecycle_mutation_allowed(goal_id=None)`
keeps today's "any goal reserved blocks a plan-wide mutation" behavior by
default for existing callers (`propose_intent`/`revise_intent`/etc., all
unchanged call sites) while allowing a goal-scoped check for future use. A
`model_validator(mode="before")` migrates a persisted plan's legacy
`promotion_reservation` field (a parseable `goal:{cycle_id}:{goal_id}` token
maps to `{goal_id: token}`; an unparseable opaque execution-id token has no
recoverable goal_id under the new keying and is dropped — there is no
consistent key a later `release_promotion` call could ever find it under
anyway, so dropping is the only coherent choice, not a corner cut for
convenience).

Everything else in the goal-parallelism work (the `goal_leases` lease/claim
schema and repository, the worker/dispatcher wiring, the cross-process
CAS-retry-safe finalize, and the cross-process git-merge lock) is app/infra-
layer, not domain, and is not folded into this unfreeze entry — the
decision log's convention is specifically about FROZEN-domain changes. See
[ADR-001](adr-001-concurrency-lease.md)'s updated status for the full
picture of what shipped.

54. **Domain unfreeze #14 (2026-07-23): symmetric per-goal leases, per-goal
blocks, single-process goal-worker pool.** A live walkthrough of unfreeze
#13 (real two-worker-process run) surfaced and fixed one genuine concurrency
bug (a goal-lease worker racing the plan-lease worker for the same task —
see the `claim_ready_goal.py` git history) and, in the process, exposed
three deliberate compromises from #13's additive/legacy-safe shape that the
user asked to remove outright rather than keep living with: (a) the lease
model was asymmetric — one "plan-level" lease always drove the single
position-earliest goal via `next_action` regardless of its own readiness,
forcing the goal-lease scan to carve that one goal out of its own candidate
set to avoid colliding with it; (b) `Plan.block` was a single plan-wide
scalar, so the instant ANY goal opened a block, the whole plan's `status`
flipped to `BLOCKED` — and since the claim SQL (`_CLAIM_SQL` /
`list_running_ids`) filters `status = 'running'`, that also made the ENTIRE
plan unclaimable, forcing every other independent, still-progressing goal to
abandon its in-flight work (observed live as a task flipping to `SKIPPED`
mid-run for a reason unrelated to that goal); (c) real parallelism required
hand-starting a second OS worker process, since one process's own loop drove
`worker_tick` and one goal via `goal_tick` strictly sequentially.

Domain-layer changes: `Plan.goal_blocks: dict[str, PlanBlock] = {}`, additive
alongside the legacy scalar `block` — `open_block`/`resolve_block` route a
block with a `goal_id`, opened while an active cycle exists, into this dict
instead (raising only on a genuine same-goal double-open, never a different
goal's); every other block (no `goal_id`, or a legacy non-cyclic plan) keeps
the original scalar behavior, byte-identical. A new shared DAG primitive,
`dependency_graph.blocked_nodes` (same shape as the existing `ready_nodes`),
and `navigation.plan_can_progress` compute whether ANY non-terminal goal can
still make progress; `Plan._recompute_cyclic_status` (called after
`open_block`/`resolve_block`/`complete_goal`) sets `status = BLOCKED` only
when every non-terminal goal is blocked or transitively depends on one that
is — not the instant any single goal blocks — with an explicit short-circuit
for "zero non-terminal goals" (a finished cycle, not a stuck one).
`retry_task`/`retry_agent_binding` were extended to resolve whichever
location (scalar or per-goal) actually holds the relevant block, since a
goal-enrichment `agent_capability` block now also routes per-goal.
`peek_next_for_goal` gained a terminal-goal short-circuit (returns `None`
once its own goal is already DONE/terminal) paired with a fix in
`ExecutionHandler.handle`'s shared body so a goal-lease worker's `None`
never triggers the plan-wide `_enter_review` transition just because ITS
goal finished while siblings are still running — a latent gap from #13's
original `handle_goal` sharing that only surfaces once every goal (not just
the non-privileged ones) drives through the goal-lease path.

App/infra layer (not itself domain, noted here for the full picture):
`advance_plan.py`'s cyclic branch no longer dispatches execution at all —
only enrichment fan-out and the "every goal terminal → enter review"
transition; ALL execution dispatch, including the position-earliest goal,
goes through `claim_ready_goal` symmetrically (the #13 exclusion carve-out
is dead code, removed). `claim_ready_goal` also excludes any goal with an
active `goal_blocks` entry, so a blocked goal is never re-claimed and
re-collided with its own still-open block. `infra/worker/main.py` gained an
in-process goal-worker pool (`max_concurrent_goals`, new CLI flag, default
4): each `run_worker_forever` process now claims and drives multiple ready
goals concurrently via its own asyncio tasks, each with its own fresh
`UnitOfWork` (never the shared one `worker_tick` uses) — a single
`orchestrate worker start` process is now sufficient for real goal-level
parallelism; running more processes remains supported for horizontal/
multi-host scaling but is no longer required to demonstrate it.

**Addendum (2026-07-23, same unfreeze):** `status_reason`/`legal_actions` no
longer mask coexisting per-goal blocks behind an active plan-wide scalar
block — the scalar block stays the headline (its kind/code/resolutions lead),
but the summary message notes how many goals are independently blocked and
the per-goal blocks' legal resolutions are unioned in. Presentation-only
change to derived read-model properties; no state shape or transition change.

---

**Decision 55 (2026-07-23) — domain unfreeze #15: per-kind retry budgets.**
`RetryPolicy` gains `kind_max_attempts` and `kind_backoff_scale` so
self-healing failure kinds stop exhausting into operator-facing blocks:
`rate_limit` now retries up to 6 attempts on a 4x-scaled (patient) backoff
curve and ceiling, `connection_error` up to 5; all other kinds keep the
uniform `max_attempts` budget and curve, and `non_retryable_kinds` is
untouched. Additive Pydantic fields with defaults — persisted pre-#15
policies rehydrate unchanged. Motivation: block-frequency UX (blocks are
automation's give-up signal; a kind that heals by waiting should almost
never produce one).

---

**Decision 56 (2026-07-25) — domain unfreeze #16: provider capacity metadata.**
`ModelProvider` gains `max_inflight` and `capacity_scope`; `IAModel` gains
`max_inflight`. All optional with `None` defaults, so persisted rows rehydrate
unchanged and unset values fall back to the global config keys.

An in-flight ceiling is a property of the PROVIDER, not of the orchestrator: a
paid tier, a free aggregator, and a local single-GPU server share no sensible
number, and one global value would either throttle the paid tier to free-tier
levels or over-drive the local one. `capacity_scope` (`per_model` |
`endpoint_wide`) records whether the provider's upstream limits are per routed
model — an aggregator fans each model out to its own inference pool while
billing and daily caps stay account-wide — or shared across a single endpoint,
as in a self-hosted deployment serving several models from one pool.

The point of putting these on the catalog entities rather than in namespaced
config keys is that capacity is provider *data*: policy reads it, and no handler
anywhere branches on a provider name. Same category as unfreeze #1, which added
`runtime_type`/`provider_id`/`model_id` to `AgentSpec` so the agent registry
could own runtime resolution.

Consumed by the provider admission gate (never start an attempt that would
exceed the ceiling — turning "fire the request, get refused, back off" into
"never fire it") and by the scope-aware circuit key (account-level limits open a
provider-wide circuit, upstream-level ones stay per-model). Surfaced through the
existing provider/model CRUD API. Migration 0013 adds the columns, nullable.

---

**Decision 57 (2026-07-26) — domain unfreeze #17: a rejected candidate is not
terminal.** `RetryPolicy` drops `VERIFICATION_ERROR` from `non_retryable_kinds`
and gains `kind_attempt_ceiling` (default `{VERIFICATION_ERROR: 2}`). Additive
Pydantic field with a default — persisted pre-#17 policies rehydrate unchanged.

A ceiling is the opposite instrument to unfreeze #15's `kind_max_attempts`. That
one is a FLOOR: it grants a self-healing kind extra tries and deliberately
cannot be cut by a lower global budget. This one caps a kind whose repetition is
EVIDENCE rather than bad luck, and must therefore survive a *higher* global
budget — otherwise an operator who raises `max_attempts` to ride out rate limits
silently pays for ten identical verification failures.

Motivation, from a live Tier 1 walkthrough of `fixtures/happy-path-v1`: attempt 1
hit a provider rate limit and was correctly waited out; attempt 2 failed
`test author produced no executable checks`; the goal blocked and a human was
required. Agent output is a sample, and re-running it against the same frozen
tests is the cheapest recovery available — but only if the agent is told what was
rejected. Before this, `build_task_prompt` rendered the contract and nothing
about the previous attempt, so a retry re-ran an identical prompt against an
identical contract on a clean worktree and reproduced an identical failure at
full provider cost. That is why `VERIFICATION_ERROR` being terminal was *correct*
until the feedback loop existed, and why the loop shipped first
(`agent_orchestrator/app/agent_feedback.py`, `PriorAttemptFeedback`, rendered after
`## Constraints`).

The retryable set is now narrower than the kind: only class **A** — the
candidate the agent produced was rejected (out of scope, protected test touched,
no RED, authoritative command failed). Class **C** — orchestration races and
missing infrastructure (superseded cycle, evidence changed during promotion,
absent verifier) — stays terminal through `RuntimeFailure.retryable=False`, an
independent veto in the retry condition, so the split is structural rather than
keyed off message text. Class **B** — a contract no agent could satisfy — is
still bounded by this ceiling and then blocks; repairing the contract instead of
blocking is the next phase.

The ceiling is 2, not #15's 6: a rate limit heals by waiting, whereas a third
identical rejection says the CONTRACT is wrong, and no number of retries repairs
a contract.

---

**Decision 58 (2026-07-26) — domain unfreeze #17 (continued): an authored
contract is editable.** The same unfreeze as decision 57, second half. Three
additive domain changes, no migration — plans persist as one JSON document, so
defaulted fields rehydrate pre-#17 rows unchanged (pinned by a round-trip test
that strips the new field first).

1. **`Task.semantic_edit` widened; `Task.amend_contract` added.** A frozen
   `TaskContract` was unreachable: the eight edit types touched task
   name/description/capabilities/agent/order and goal name/description/deps, and
   only `semantic_edit` wrote a contract at all — its `revision` and `objective`.
   One wrong string therefore cost a full replan. Observed live: enrichment froze
   a `tdd` contract whose `allowed_scope` named only production files, which no
   agent could satisfy.

   The split is NOT editable-vs-not, it is whether a change alters what "correct"
   MEANS. `objective`, `acceptance_criteria` and `verification_strategy` do —
   criteria are what `freeze_test_bundle` maps to checks and the strategy decides
   what evidence is meaningful — so they bump the revision and invalidate the
   `TestBundle`. `allowed_scope`, `forbidden_scope`, `verification_commands`,
   `goal_criterion_ids` and `required_capabilities` do not, so `amend_contract`
   keeps the authored tests. Re-authoring a suite to fix a typo in a command is
   precisely what made a replan look cheaper than an edit. Evidence is cleared
   only when a previously accepted candidate might no longer qualify: commands
   changed, or scope NARROWED. Widening is provably safe and keeps it. Both
   transitions revalidate the whole `TaskContract`, so an edit cannot write a
   shape enrichment itself could never produce.

   **Not editable, deliberately:** `id`, `attempt`, `revision`, `status`,
   `result`, `verification_evidence`, `test_bundle`, and any DONE or SKIPPED
   task. These are the audit trail the finalize re-guard and "only independently
   verified work moves upward" key off; a writable `revision` would let a stale
   in-flight finalize land, and a DONE task's contract has already been merged
   upward under evidence that references it. Where a block advertises `edit_task`
   for a terminal task, the ADVERTISEMENT is the defect.

2. **`Cycle.approved_intent`.** `activate_cycle` discarded `Plan.intent_proposal`,
   and `Cycle` kept only `intent_proposal_id` — so goal enrichment, which must
   honour the intent's objective/scope/constraints/exclusions, could read nothing
   but an opaque identifier. The cycle now retains the proposal it was approved
   from, and both `read_approved_intent` and the enrichment prompt serve it.

3. **`Plan.retry_planning_stage` consults `goal_blocks`.** Goal enrichment knows
   which goal it was working on, so its failures are filed per-goal (unfreeze
   #14) — while this method read only the scalar `block`. The block advertised
   `retry_stage` as legal and the endpoint answered 422: the operator's one
   obvious move could not reach it. Cycle architecture has no goal and keeps the
   scalar. Resolution now recomputes the derived cyclic status rather than
   asserting RUNNING over a plan that may still hold other goal blocks.

Also fixed here, and the reason a stale goal contract was never noticed:
`planning_handler._enrich_one` builds each `Task` with `contract=item` where
`item` is an element of `goal.contract.tasks`, so the two start as the SAME
object, and every contract write rebinds only the task's reference.
`edit_service.resync_goal_contract` rebuilds the list after any edit and re-runs
`GoalContract`'s validator — which is the point, not a side effect: with
`goal_criterion_ids` editable, an edit can orphan a goal criterion, and that now
surfaces instead of landing silently.

**Decision 59 (2026-07-27) — domain unfreeze #18: a refusal speaks the
vocabulary the operator was shown.** `Plan.pause` and `Plan.resume` raise
`InvalidTransitionError` with `self.status.value` instead of `self.phase.value`
(`planner_orchestrator.py:419`, `:427`). No state, transition, invariant, or
persisted shape changes — the argument to an error message does. This is the
smallest un-freeze in the log and is recorded anyway, because the rule is that
domain edits are deliberate, not that they are large.

Found by driving the API as the operator during the Phase 2 control
experiments. `Plan.request_pause` — the cyclic graceful pause gate — already
reported `status.value`, so the same surface refused in two vocabularies
depending on which branch fired. A plan the API describes as
`status: waiting, reason: intent` was refused with "cannot transition from
**discovery** to resumed": the nine-phase machine that
[ADR-003](adr-003-cyclic-project-plan-lifecycle.md) superseded, and that a
cyclic operator has never seen in any other response. The legacy projection is
deliberately still there for migrated rows and existing clients, but it is a
*read* compatibility surface — leaking it into an operator-facing 422 tells
someone their plan is in a phase the rest of the API never mentions.

This matters for Phase 2 exit criterion 3 (operators can distinguish plan
states from persisted facts) for the same reason `block_policy` exists: a
control surface that misdescribes itself is worse than one that says nothing.
Locked by `test_refusals_speak_the_cyclic_vocabulary_not_the_legacy_phase` on
both backends.

**Decision 60 (2026-07-28) — domain unfreeze #19: a cyclic plan is cyclic
before its cycle exists.** `Plan.request_pause` and `Plan.pause` decide whether
a plan is governed by the cyclic lifecycle with a new `Plan._is_cyclic` (any
cycle, intent proposal, or cycle draft) instead of `active_cycle is not None`.
No state, persisted shape, or transition is added — one predicate is replaced by
a more accurate one.

`active_cycle is not None` was standing in for "is this a cyclic plan", and the
two are not the same question. Between an approved intent and an activated
cycle a plan is fully cyclic and has **no cycle yet**: `status` is RUNNING,
`activity` is `cycle_architecture`, and the legacy `phase` is still `discovery`
because cyclic planning never advances the compatibility projection. The guard
fell through to that phase, found it outside `WORKER_CLAIMABLE_PHASES`, and
refused — while `_CLAIM_SQL` considered the same plan claimable and
`Plan.legal_actions` advertised `pause`. An operator watching a plan the API
described as `status: running, legal_actions: ["pause", "start_replan"]` pressed
pause and got 422 `INVALID_TRANSITION`, in the exact window they most want it:
waiting on the planner.

Found by the Phase 4 advertised-action contract test
(`tests/integration/test_legal_actions_contract.py`) on its first run — the test
Phase 4's exit criterion *every advertised action works in the state that
advertises it* exists to produce. That criterion is why the fix belongs in the
domain rather than in the API: narrowing `legal_actions` instead would have made
the two agree by removing a control that should work, and the claim predicate
already treats the plan as live.

The legacy half is unchanged and locked: a plan with no cyclic artifact is still
judged by its phase, so a legacy row in REPLANNING still refuses to pause.
Locked by `test_a_cyclic_plan_awaiting_architecture_can_pause` and
`test_a_legacy_plan_is_still_judged_by_its_phase`.

**Decision 61 (2026-08-02) — authenticated GitHub publication is promoted out
of the deferred list into Phase 8 (P8.1). No domain unfreeze.**
`OutputDisposition.OPEN_PR` recorded that a human opened a pull request;
`output_reference` was free text they typed. The orchestrator now pushes
`cycle/<id>` and opens the pull request itself, so that reference is a fact it
produced.

The trigger was a contradiction inside Phase 8's own wizard deliverable, which
requires that "declining the token must downgrade the delivery method". That
presumes a delivery method a token *changes*, and none existed. Shipping the
token step first would have collected a credential nothing reads — which is the
workaround the constraint exists to forbid. It also answers open question #3 of
the 2026-08-02 delivery analysis: `output_reference` was "the one place a human
asserts something the system cannot verify".

**Bounds, chosen so promoting a deferred item does not become open-ended:**

- **GitHub only.** One adapter behind `app/forge_port.py`. Guessing GitLab or
  Gitea semantics with no user asking is the completeness the roadmap's scope
  discipline forbids.
- **Opens a pull request, never merges one.** There is no merge method on the
  port, so the guarantee is structural rather than documented; `MERGE` stays a
  recorded human claim. Automatic merging remains rejected.
- **Pushes `cycle/<id>` and nothing else.** The default branch is still never
  written by plan work.
- **`NoForge` is a permanent fallback, not a placeholder** — the `NoSandbox`
  principle. An installation with no token keeps recording the disposition an
  operator typed; that is a supported configuration.

**Why no unfreeze was needed.** The binding could have become fields on
`ProjectDefinition`, which is frozen. It did not: the config store is already
two-tier with a project id as a scope, so `forge.provider` / `forge.repository`
/ `forge.token_ref` live there, and the token in the existing secret store under
`secret://forge/<project_id>`. Per project rather than global, because two
projects can live on different accounts and one credential spanning every
project is the wrong blast radius for a tool running unsandboxed agents.

**The ordering is the load-bearing part.** `record_output_disposition` does
everything in one transaction, which is correct for a claim a human typed. A
push and an API call are side effects, and invariant #5 forbids those inside a
transaction — so `app/use_cases/publish_cycle.py` reads (transaction opens and
closes), pushes and opens the PR (outside), then records the disposition with
the real URL (new transaction, re-read and re-guarded). A forge failure leaves
the gate open with nothing written, and `retain_branch` is still available.
Locked by `tests/integration/test_publish_cycle.py`, including the retry.

The token is verified at save time against the exact repository — one call
confirming it exists, is reachable, and can push — so a read-only credential
fails at setup rather than at a publication gate at the end of a cycle.

**Decision 62 (2026-08-02) — the cycle acceptance run is an application port
and an advisory ledger, NOT domain state. No unfreeze.** `ProjectEnvironment`
(`app/environment_port.py`, beside `sandbox_port.py`) boots a cycle's assembled
tree and runs the operator's scenario against it; the verdict lands in
`acceptance_runs` (migration 0018) and is served on the cycle evidence
endpoint.

**Why no domain change was needed**, checked at the three points where one
would have been forced:

- *Does the gate need to display it?* No — the read model joins the ledger.
- *Does the verdict gate anything?* No, and deliberately. A flaky acceptance run
  that could withhold publication costs more trust than it earns, and
  `start_replan` is already the "fix it instead" path — so no new
  `OutputDisposition` value. **This is the load-bearing reason.** A verdict that
  blocked would have to be domain state; an advisory one need not be.
- *Does `cycle_verification` need a stored value?* No — `Plan.activity` already
  DERIVES it from "all goals terminal and no gate open".

**The ordering is what makes the port sufficient, and the first attempt got it
wrong.** `Plan.activity` checks `review_gate` before falling through to
`cycle_verification`, so running the acceptance after the gate opened reported
`review:cycle_completion` for the whole run and left `cycle_verification`
naming an empty slot — the very slot the feature claimed to fill. It also left
the gate open for the minutes a container boot takes, so a disposition could be
recorded against a verdict that did not exist yet. Running it in the window
BEFORE the gate opens fixes both, and that window is exactly where the existing
derivation already emits `cycle_verification`. **Do not move this back.** Locked
by `test_the_gate_is_not_open_while_the_acceptance_run_executes` and
`test_the_pre_publication_run_fills_the_cycle_verification_slot`.

Acceptance and gate-opening happen in the SAME tick. Keying "already done" on a
ledger row alone re-triggers forever, because a `skipped` or raising adapter
records no row — caught by the tests on the first attempt.

Two further placements avoid the domain: the environment spec lives in the
project-scoped config store (as the forge binding does, decision 61), and the
repository path is resolved through an injected callable rather than a new
method on the FROZEN `Workspace` port — mypy caught that reach.

The `DockerEnvironment` adapter is deliberately absent: the development
environment cannot run nested containers (13 masked `/proc` submounts against a
missing `CAP_SYS_ADMIN`), and shipping an unexercised container adapter behind a
green suite is the evidence-free claim this roadmap exists to prevent. The
adapter must also take its container binary from configuration rather than
hardcoding `docker` — rootless podman reached the kernel wall and is
CLI-compatible.

---

**Decision 63 (2026-08-09) — the development environment moves from a hardened
devcontainer to a libvirt/KVM guest. No unfreeze; nothing here touches the
domain.** `.devcontainer/` is deleted; `infra/dev-vm/` provisions `aipom-dev`
(Ubuntu 24.04 on `qemu:///system`, cloud-init, `make up|start|ssh|verify|destroy`).

**The reason is that the requirement is self-contradictory for a container.**
The development environment must be **privileged enough to nest containers** —
P8.5's `ContainerEnvironment` adapter cannot be validated anywhere else — and
**isolated enough to contain agent-written code**, since agents write and
execute code here. A container cannot be both: every capability granted to
satisfy the first weakens the second, and the devcontainer's hardening
(masked `/proc`, read-only `/sys/fs/cgroup`, a restrictive seccomp profile,
bubblewrap) was exactly what made nested containers impossible. A VM is both by
construction — the hypervisor is the boundary, so the guest's interior can be
as privileged as the workload needs.

**The blocker this retires was recorded wrong, and the correction is the point.**
ROADMAP's *Containerization is unavailable* claimed one final kernel wall: 13
masked `/proc` submounts forbidding a fresh `procfs`, and therefore a private
PID namespace. It was **two walls that deadlocked each other**. The read-only
cgroup2 tree forced `--cgroups=disabled`, which itself disables the private PID
namespace — so each workaround re-broke what the other needed. Neither alone
was terminal. A hand-rolled OCI bundle **did** run a container in the
devcontainer; what it could not do was run one *with isolation*. The honest
claim is about isolation, not about containers, and it matters for the adapter:
a readiness check that only asks "did a container start" would have passed in
an environment that could not contain anything.

A third finding is recorded so the instrument is not trusted again: the
cgroup-mount check used `unshare -Urm`, leaving the process in the **initial**
cgroup namespace, where mounting cgroup2 needs `CAP_SYS_ADMIN` over that
namespace's owning userns — it returned `EPERM` on *any* host, however capable.
`-C` fixes it. In the devcontainer that false red read as corroborating the
cgroup wall rather than as a broken instrument.

**The gate, not the argument, is what licenses Stage 2.** `infra/dev-vm/verify.sh`
asserts the six capabilities the devcontainer denied and returns **7 passed,
0 failed** on kernel 6.8.0-137, re-run after a kernel upgrade and a full power
cycle so the sysctl is proven to apply at boot. The unit suite runs green in the
guest (`733 passed, 1 skipped`). P8.5 is unparked.

**One deliberate relaxation, and its blast radius.** Ubuntu 24.04 ships
`kernel.apparmor_restrict_unprivileged_userns=1`, which blocks `bwrap` and
`unshare` from a plain shell while podman and docker pass on their own AppArmor
profiles — a guest that runs PID-isolated containers perfectly while several
checks fail exactly like a kernel wall. Cloud-init disables it
(`/etc/sysctl.d/60-aipom-userns.conf`). That is sound *here and only here*: the
VM boundary — **not bubblewrap** — is now what contains agent-written code, the
host kernel is untouched, and the guest is cattle (`make destroy && make up`)
rather than a pet. All durable state lives in `~/.orchestrator`. See
`infra/dev-vm/README.md` for the threat model.

**Why no unfreeze:** this is environment and tooling. No domain module,
aggregate, port or invariant changes. The `ContainerEnvironment` adapter it
unblocks plugs into `app/environment_port.py`, which decision 62 already placed
outside the domain.
