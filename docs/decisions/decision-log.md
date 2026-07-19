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

PR gate · project spec governance · decision gate · GitHub PR output · parallelism · env provisioner · Postgres · Redis claim path · pi NDJSON streaming. Details and reintroduction designs: [../legacy/pre-refactor-backend.md](../legacy/pre-refactor-backend.md); scheduling: [ROADMAP.md](../../ROADMAP.md).


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
