# Master Roadmap (FINAL) — Clean Domain → Integrated Prototype → Launch

The complete, gap-closed plan. Every design decision from the build conversation is
folded in; the 47-requirement audit is satisfied; the phase machine, loop-back, and
mutation-safety are fully resolved. Six phases in mandatory order. Each has a Definition
of Done. **Locked** decisions are stated as fact; the few remaining **‹DECIDE›** are
phase-local and noted; **⚠** marks risks/nuances.

> **Entering state:** `domain/` + `application/` complete and clean (pyflakes-clean,
> single repository contract, shared lookups), **87 tests passing**, fully documented
> (per-layer READMEs + INTEGRATION_GUIDE). Sequential pull-based orchestration;
> reconciler removed; durable backoff gate, per-plan lease, transactional outbox,
> agent_events, scriptable dummy runner all in place. **`advance_plan` refactored into
> a thin phase DISPATCHER + handlers** (`ExecutionHandler` / `GateHandler` /
> `PlanningHandler` seam, `Signal` enum) — the reasoner phases land in
> `PlanningHandler` (2.5) without touching execution. Infrastructure, API, frontend,
> worker, scheduler NOT built.
>
> **r2 review:** this revision was audited against the running code; two live bugs were
> found and verified by repro (gate spin, worker-tick spin), plus four design holes
> (RUNNING→DONE vs REVIEW, missing `Goal.skip`, the abandoned-iteration resurrection,
> the undefined driver/claimability model) and one unspecified mechanism (outbox
> relay). All are folded in below, marked 🐛/r2.

---

## THE PHASE MACHINE (the spine — decided, build to this)

Nine phases:

```
   ┌──────────────────── REPLANNING re-enters the build pipeline ────────────────────┐
   ▼                                                                                  │
DISCOVERY ─► ARCHITECTURE ─► ENRICHING ─► AWAITING_REVIEW ─► RUNNING ─────────► REVIEW
(first plan   (structure      (detail      (gate BEFORE        (execute goals    (gate AFTER
 from brief,   into goals)     goals/tasks)  execution:          sequentially)     execution:
 iter 1)                                     approve/edit/                          finish or
                                             send-back)                             replan?)
                                                  │                                    │
                                    "send back"   │                       "finish" ────┴─► DONE
                                    (manual edit) │                       "replan"  ───┐
                                                  ▼                                     │
                                            ARCHITECTURE                                │
                                                                                        │
   mid-RUNNING, user chat "give me a new plan":                                         │
        request_replan ─► pause worker ─► SKIP incomplete goals ─► REPLANNING ◄─────────┘
                                                                   (conversational re-plan
                                                                    WITH the user, using prior
                                                                    iteration's DONE results +
                                                                    chat input as context)
                                                                        │
                                                                        ▼
                                                                  ARCHITECTURE ─► ... (runs again,
                                                                                       different goals)
   any phase can FAIL ─► retry-with-backoff ─► exhausted/non-retryable ─► FAILED
```

**Phase definitions (lock these semantics):**
- **DISCOVERY** — the *first* plan, reasoned from the brief. No prior context. Iteration 1.
- **ARCHITECTURE** — structure the plan into an ordered roadmap of goals.
- **ENRICHING** — fill goal/task detail. *Kept as a separate phase from ARCHITECTURE
  for crash-recovery granularity* (more phase boundaries = more checkpoints = finer
  resume). Semantically one activity in two steps; that's fine — each is a checkpoint.
- **AWAITING_REVIEW** — human gate BEFORE execution: approve, edit (→ surgical
  `apply_edit`), or send back to ARCHITECTURE.
- **RUNNING** — execute the iteration's goals sequentially (the pull-scan core).
- **REVIEW** — human gate AFTER execution: finish (→ DONE) or replan the next phase
  (→ REPLANNING).
- **REPLANNING** — *conversational* re-plan WITH the user, using prior context
  (completed goals' results + chat dialogue). Reached two ways: from REVIEW (post-
  iteration) or from RUNNING (mid-course, user chat). Flows into ARCHITECTURE like
  DISCOVERY but with different, context-aware content. **This is what "refining" means
  — conversational re-planning, NOT manual editing.**
- **DONE / FAILED** — terminal.

**Two re-plan entry points, one phase:** REPLANNING is reached from REVIEW ("replan
next phase") and from RUNNING (user chat "new plan"). Both land in REPLANNING and flow
the same way downstream.

**The loop is append-only (key domain mechanic):** each iteration's new goals are
*appended*; prior DONE goals stay as history AND as context for the next re-plan.
`next_action` already skips terminal goals, so the growing goals list is walked
unchanged. An **`iteration` counter** on the plan distinguishes iteration N's goals
(and later maps to git-flow releases — context only for now).

**Mid-RUNNING replan abandons in-flight work (DECIDED — option a):** when the user
triggers a replan mid-execution, incomplete goals are **SKIPPED** (not finished first);
REPLANNING generates a fresh set; completed goals remain as history/context.

**Two DISTINCT user capabilities — do not conflate:**
- **`apply_edit`** = surgical, manual, granular ("change this task"). Status/lease
  guarded. *Already built.* The "edit/send-back" at AWAITING_REVIEW.
- **`request_replan`** = conversational, holistic ("give me a different plan"). A phase
  transition. *New.* The "refining" the user means.

---

## THE DRIVER MODEL (r2 review — who advances each phase; claimability)

The phase machine said *what* the phases are but not *who drives them*. Left implicit,
this produces a real bug: a worker that claims a plan in a **conversational** phase
(DISCOVERY/REPLANNING needs user chat) cannot advance it → endless claim/release churn.
The model, made explicit:

| Phase | Driven by | Worker-claimable? |
|---|---|---|
| DISCOVERY, REPLANNING | **chat/API request** (conversational reasoner turns; each user message advances the dialogue via a use case) | **NO** |
| ARCHITECTURE, ENRICHING | **worker** (autonomous reasoner steps, no user input) | YES |
| RUNNING | **worker** (the pull-scan execution loop) | YES |
| AWAITING_REVIEW, REVIEW | **human command** (approve / send-back / finish / replan via API) | **NO** |
| DONE, FAILED | terminal | NO |

So `claim_one_unit`'s predicate = **phase ∈ {ARCHITECTURE, ENRICHING, RUNNING}** (plus
lease-free). Gates and conversational phases are simply invisible to workers — same
principle as the pull-scan: what isn't ready is never selected, so it never churns.
(The current in-memory fake claims planning phases indiscriminately — fix with this.)

**The abandoned-iteration rule (r2 — closes a verified resurrection bug):** on a
mid-RUNNING replan, the *active* goal cannot be skipped (it has a RUNNING task) and a
late failure requeues its task to PENDING. Since `next_action` scans in position order,
that stale goal **would be re-executed after the next iteration starts**. Therefore:
"skip incomplete goals" is enforced in TWO places — (1) at `request_replan`: skip all
PENDING goals/tasks; (2) at **REPLANNING completion** (before appending new goals):
finalize-abandon any remaining non-terminal prior-iteration goal — skip its PENDING
tasks, then close the goal as SKIPPED. Late in-flight results: success finalizes
normally (task still RUNNING → DONE, harmless history); a late *failure* must
**terminal-skip, not requeue**, when the plan is no longer RUNNING (tolerant finalize:
the failure txn checks `plan.phase`).

---

## Phase 0 — Finish & FREEZE the domain

Everything that touches the domain happens here, before the freeze. Nothing changes the
core after this.

### 0.1 Finish remaining domain work
- [ ] Action all collected refactor TODOs.
- [ ] **Add the phase machine** to the domain: the 9-phase enum
      (`DISCOVERY, REPLANNING, ARCHITECTURE, ENRICHING, AWAITING_REVIEW, RUNNING,
      REVIEW, DONE, FAILED`), replacing/renaming the current
      (`DRAFTING/BREAKDOWN/ENRICHING/AWAITING_REVIEW/EXECUTING/DONE/FAILED`). Map:
      DRAFTING→DISCOVERY, BREAKDOWN→ARCHITECTURE, EXECUTING→RUNNING, keep ENRICHING/
      AWAITING_REVIEW, add REPLANNING + REVIEW. Update the dispatcher's phase groups
      (`_PLANNING_PHASES`/`_GATE_PHASES`) to match.
- [ ] **🐛 RUNNING completion → REVIEW, not DONE (verified mismatch):**
      `ExecutionHandler` currently calls `plan.mark_done()` when the scan returns None.
      In the 9-phase machine, exhausting the goals transitions to **REVIEW** (the
      post-exec gate); DONE is reached ONLY from REVIEW "finish". Change the handler +
      emit `PhaseAdvanced` instead of `PlanCompleted` there (`PlanCompleted` moves to
      the REVIEW→DONE transition).
- [ ] **🐛 Gates ALWAYS pause (verified spin bug):** a plan at AWAITING_REVIEW spins
      `drive_plan` to max_steps today, because `should_pause()` checks membership in
      `pause_after={ENRICHING}` and gates aren't in it → CONTINUE forever. Fix:
      `GateHandler` returns PAUSED **unconditionally** for AWAITING_REVIEW/REVIEW;
      **remove/repurpose `pause_after`** (its job — "checkpoint after ENRICHING" — is
      now expressed by AWAITING_REVIEW being a phase; the field is obsolete and
      confusing).
- [ ] **🐛 Add `Goal.skip()` (verified missing):** the decided "skip incomplete goals"
      is unimplementable — Goal has only start/complete/fail. Add a guarded `skip()`
      (from PENDING; and from RUNNING **only when all its tasks are terminal** — the
      finalize-abandon path).
- [ ] **Tolerant finalize (late results after replan):** the task-failure txn must
      check `plan.phase` — if the plan left RUNNING (replan happened mid-flight),
      terminal-skip the task instead of requeueing it into an abandoned iteration
      (see the abandoned-iteration rule above). Success-path finalize stays as-is.
- [ ] **Add `iteration: int` to the Plan aggregate** (starts 1; increments **when
      REPLANNING commits its new goal set** — one defined point, not at request time).
- [ ] **Loop-back transitions** in the aggregate:
      - REVIEW → "finish" → DONE (emits `PlanCompleted`); REVIEW → "replan" → REPLANNING.
      - REPLANNING → ARCHITECTURE (after the conversational re-plan produces goals),
        preceded by **finalize-abandon** of prior non-terminal goals.
      - append-only goal addition (new goals get positions after existing ones; DONE
        goals untouched).
- [ ] **`request_replan` use case** (state machinery only; reasoning is Phase 2):
      skip PENDING goals/tasks (the RUNNING one finalizes via tolerant finalize),
      transition to REPLANNING.
- [ ] **Refinement/replan distinction is clean**: `apply_edit` (surgical, exists) vs
      `request_replan` (new). No "REFINING" phase.

### 0.2 Concurrency ADR (required even though sequential now)
- [ ] Write it: **per-plan lease now** (one worker owns a plan → sequential). The lease
      *granularity* IS the *unit of parallelism* — moving the lease down to goal (goals
      concurrent, tasks sequential) or task (full parallel) is the future switch, which
      also requires `next_action` to return a *set* of ready units + a workspace-
      conflict strategy. Record this so the seam is intentional.

### 0.3 Settle the last domain decisions (before freeze)
- [ ] **‹DECIDE B› rebind-on-edit (LOCK NOW):** manual `agent_id` edit = explicit
      override (no auto-rematch); editing `required_capabilities` = re-run `match_agent`.
- [ ] Confirm prior-iteration results are retrievable for REPLANNING context — they live
      on DONE tasks' `result` fields, so they already are. ✓

**DoD:** TODOs cleared; 9-phase enum + iteration counter + loop-back transitions +
`request_replan` state machinery in the domain; concurrency ADR written; rebind locked;
0 lint; green tests (update the existing 87 for the renamed/added phases);
**`INTEGRATION_GUIDE.md` updated to the frozen contracts** (9-phase claim predicate,
Goal.skip, tolerant finalize, RUNNING→REVIEW, new use cases) so Phases 1–2 execute
against a current guide.
**🔒 DOMAIN FREEZE.** No core changes after this point.

---

## Phase 1 — Integration plan (planning only, no code)

Produce the detailed integration document.

### 1.1 Feature triage (AI-assisted, against the concrete current workflow)
Per old-repo capability: keep / defer (+ rationale + roadmap entry + preserved seam):
- **git-branching workspace → KEEP** (it's the workspace rollback you require).
- **PR gate → DEFER** (local-dir + zip outputs cover the prototype; shelve cleanly).
- **project spec → DEFER** (governance, not core-flow critical).
- **decision gate → DEFER but KEEP DESIGNED** (genuine whitespace; document as a
  first-class future feature with its `Decision` artifact + gate — shelve, don't drop).
- dashboard / config / pi runner / SQLite / **existing secrets encryption** →
  **PRESERVE & ADAPT** (become adapters behind the new ports).

### 1.2 Conflict list (old vs new) + resolution
- reconciler → **delete** (flow half via pull-scan; crash half is the lease).
- push-dispatch / goal-manager dependency-resolution → **delete** (`next_action` owns it).
- task-manager/goal-manager split → collapse into the single pull-loop.
- old tests asserting push-and-reconcile → **rewrite/delete**.

### 1.3 Preservation list → port map
Each preserved infra piece mapped to the port it implements (incl. the existing
encryption mechanism → the secrets concern in 2.2).
**The concrete handbook for this is `INTEGRATION_GUIDE.md`** — it holds the exact
port→adapter contracts (the version-CAS SQL shape, the lease claim query, the runner
contract, the worker entrypoint wrapper, the API→use-case mapping, and the
verification procedure). The integration plan produced in this phase references it
rather than restating it; the guide is **updated at Phase 0 exit** (the domain freeze
changes contracts: 9-phase enum, Goal.skip, tolerant finalize, driver-model claim
predicate) so Phase 2 executes against a current guide, never a stale one.

### 1.4 Data migration — CLEAN BREAK (decided)
Old data is **thrown away** (it's test data). New schema starts fresh. No migration
script. (Optionally hand-recreate a couple of agents/providers for convenience — not
required.)

### 1.5 Integration rollback — USER-HANDLED (decided)
You manage your own git branches for the integration. Not a roadmap concern. (Standard
practice: integrate on a branch, keep old runnable until Phase 5 green — but you own this.)

### 1.6 Modularization plan
Structure so deferred features (PR gate / spec / decision gate) graft on later without
re-coupling. Define module boundaries + extension points now.

**DoD:** integration doc with feature-triage table, conflict→resolution list,
preservation→port map, deferred-feature module boundaries, ordered task list. (Data
migration + integration rollback are decided/owned — note them, don't belabor.)

---

## Phase 2 — Infrastructure adapters (run the proven core)

Implement adapters behind the ports the application already exercises; re-run the
in-memory suite against each real adapter as it lands. **Work from
`INTEGRATION_GUIDE.md`** — it is the per-port contract handbook for everything in
this phase (exact method contracts, SQL shapes, the deletion list, and the
verification procedure). The items below are the roadmap view; the guide is the
implementation view. If they ever disagree, fix the guide first, then implement.

### 2.1 Persistence — SQLite (WAL)
- [ ] `PlanRepository`: version-CAS save; `find/bind_request_id`; lease
      (`claim_one_unit`/`heartbeat`/`release` + `claimed_by`/`claimed_at` columns);
      reconstruction via `PlanFactory.reconstruct`; **`retry_not_before` column**;
      **`iteration` column**; goals/tasks `ORDER BY position`.
- [ ] Reference repos (agent/model/provider/capability/project): full CRUD + integrity —
      **delete-guard** (`ReferencedEntityInUseError`), provider→model **cascade-down /
      guard-up**, dangling-ref net.
- [ ] `UnitOfWork`: real transaction making `repo.save` + `outbox.add` **atomic**.
- [ ] `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.
- ⚠ **INTEGRATION TRUTH-TEST** (the single most important verification): re-run
  `test_advance_plan` + `test_worker_loop` + `test_backoff_gate` against the REAL SQLite
  UoW. The crash-recovery + outbox-rollback + backoff-gate-survives-crash tests prove
  transactional atomicity is REAL, not simulated by the in-memory fake. If these pass on
  real SQLite, the core is trustworthy.

### 2.2 Secrets — reuse existing encryption (decided)
- [ ] API keys stored ENCRYPTED, reusing the old repo's existing encryption mechanism
      (not plaintext in SQLite). Never log/telemetry key values (extend the
      "no secrets in error context" rule to telemetry).

### 2.3 Clock
- [ ] Real adapter (`datetime.now(timezone.utc)`) — required by the backoff gate.

### 2.4 Agent runtime — pi as per-run stdio subprocess
- [ ] `AgentRunner`: spawn pi per run; stdio handshake (inject tool manifest + agent
      spec + cwd); stream NDJSON → `AgentEventSink` (tagged by attempt); success →
      `TaskResult`.
- [ ] **Error classification (#12) — the SHARED FAILURE TAXONOMY** (one constant set,
      used by BOTH the real runner and the dummy): `connection_error`, `rate_limit`,
      `token_limit`, `auth_error`, `timeout`, `tool_error`. Wire
      `RetryPolicy.non_retryable_reasons`: **token_limit / auth_error = terminal**;
      **rate_limit / connection_error / timeout = retryable**.
- [ ] **Backoff mechanism**: the worker/runner performs the wait the durable gate
      scheduled (decision in domain, wait in orchestration cadence — never a domain sleep).
- ⚠ pi stdio contract specifics: verify against the REAL pi build; isolate in one
  `pi_protocol` module so a contract change is a one-file fix.

### 2.5 Reasoner — OpenAI + the planning phases + loop-back
- [ ] `Reasoner` for DISCOVERY, ARCHITECTURE, ENRICHING, **and REPLANNING**:
      - DISCOVERY: brief → plan.
      - ARCHITECTURE: plan → ordered goals; **emit `required_capabilities` per task**
        (feeds `match_agent`).
      - ENRICHING: goals → detailed goals/tasks.
      - **REPLANNING: prior DONE results + chat dialogue → new goal set** (conversational;
        this is the context-aware re-plan that distinguishes it from cold DISCOVERY).
- ⚠ Wiring the reasoner into the phase machine is **new application work** — `advance_plan`
  currently only pauses/continues on non-RUNNING phases. The phase-machine driver
  (which Reasoner call per phase, the REVIEW→REPLANNING→ARCHITECTURE loop) is built here.

### 2.6 Agent & reference-data use cases (#17/#18/#19)
- [ ] **`rebind_task_agent(plan_id, goal_id, task_id, agent_id)`** — reassign a task's
      agent; only if the task is not RUNNING. #17.
- [ ] **Granular agent editing** — edit an `AgentSpec` (instructions/capabilities/
      model_role) only if no RUNNING task is bound to it (else busy/in-use error). #18.
- [ ] Provider / model / API-key CRUD (#19) with the encrypted secrets handling (2.2).

### 2.7 Workspace — local strategies + rollback
- [ ] Local-directory adapter (the "go straight to the repo dir" output).
- [ ] **git-branching workspace = the real rollback**: begin = branch/worktree,
      commit = merge, discard = reset/delete. **Stateless task exec + rollback-on-edit
      (#27)**: an edit during a running task → workspace discard + requeue (the task
      re-runs clean).
- [ ] Output strategies: (a) **zip artifact** (no env deps), (b) **local repo dir**,
      (c) GitHub PR = **DEFERRED/stubbed** (GitHub not functional this phase, #24).
- [ ] **Repo-access design (#24)**: a clean abstraction over the project repo + its
      branches (read-only inspection now), behind a port so GitHub plugs in later.

### 2.8 Config — two-tier (#20)
- [ ] **Orchestrator config**: environment settings + **dependency verification**;
      settable via API or CLI.
- [ ] **Per-project config**: settable via API or CLI; includes the framework/dev-tool
      questionnaire (#37). The **env provisioner (uv/Bun, #36) is DEFERRED** to a late
      step — design the config fields now, implement provisioning later.

**DoD:** every port has a real adapter; the full in-memory suite re-runs GREEN against
real SQLite + dummy; pi runner works on a real trivial task; the shared failure taxonomy
is wired and verified per type; secrets encrypted; rebind + granular agent edit working;
zip + local-dir outputs working; the phase-machine driver (incl. REPLANNING loop) works;
two-tier config via API+CLI.

---

## Phase 3 — Worker, scheduler, mutation guards, interrupt/edit/replan

### 3.1 Redis worker + queue (#33) + the worker-loop fixes (r2)
- [ ] Move the command/claim path to Redis (the `CommandBus`/queue seam). Worker
      restartable; crash recovery via the lease.
- ⚠ Planned addition (we built a SQLite inbox); the `CommandBus` port makes it a swap.
  **Optional simplification: keep the SQLite inbox through Phase 2, swap to Redis here.**
- [ ] **🐛 Fix the worker-tick spin (verified):** `worker_tick` returns `True` whenever
      it claimed a plan — even if `drive_plan` immediately returned
      `not_ready`/`paused`. The outer loop only sleeps on `False`, so a single
      backing-off plan produces a **hot claim→release CPU spin** until its gate expires.
      Fix: make the tick's result reflect *progress*, not *claiming* — return
      False/no-progress (→ caller sleeps) when the drive signal was `not_ready` or
      `paused`; True only when actual work advanced.
- [ ] **Claim predicate = the driver-model matrix** (phase ∈ {ARCHITECTURE, ENRICHING,
      RUNNING} + lease-free). Gates and conversational phases are never claimed.
      Optional elegance (defer unless churn hurts): a `next_wake_at` column (min
      `retry_not_before` over ready work) so fully-backing-off plans aren't claimable
      at all; the tick-fix alone makes churn poll-cadence-bounded, which is acceptable.
- [ ] Heartbeat note for the real adapter: `heartbeat()` runs OUTSIDE the `with uow`
      blocks in `drive_plan` — implement it as its own short write (own connection/
      txn), not something that assumes an open transaction.

### 3.2 Scheduler / launcher ("hydra") (#34/#35)
- [ ] Launcher starts the worker; if the **worker** crashes/hangs, the lease expires and
      the launcher starts another to continue the plan (resume from persisted state).
- [ ] **Scheduler-crash resilience (decided)**: a **thin, idempotent, OS-supervised**
      scheduler (systemd / process manager restarts *it*; the lease handles worker death).
      No distributed consensus. **Document the failure modes.**

### 3.3 Sequential now, concurrency documented (#42)
- [ ] Orchestrator runs **sequentially** (per-plan lease). The concurrency ADR (0.2) is
      the deferred parallel path (lease → goal/task granularity).

### 3.4 Interrupt / edit / replan at any stage (#15/#27)
- [ ] **pause → edit → resume** (`apply_edit` + version-CAS + `control`) wired through
      API/CLI/worker. Stateless task exec: an edit during a running task rolls that task
      back (workspace discard + requeue).
- [ ] **`request_replan` wired** (the chat-triggered mid-RUNNING entry to REPLANNING):
      skip PENDING goals → REPLANNING; the in-flight task finalizes via **tolerant
      finalize**; **finalize-abandon** at REPLANNING completion closes whatever
      remained (the two-place skip rule from the driver-model section — Phase 0 built
      the machinery; here it's wired to the worker + API/chat).

### 3.5 Mutation-safety guards — extend the status/lease pattern (#16; NO global stop)
DECISION (locked): **no global "stop everything" system.** version-CAS + edit-guard +
delete-guard already prevent corruption/version-mismatch. Extend the same pattern:
- [ ] **Task edit/delete** — only if the task is not RUNNING → else `TaskRunningError`.
- [ ] **Goal edit/delete** — already gated by `_assert_editable`.
- [ ] **Plan DELETE** — gated by the **lease**: a live-lease plan → `PlanBusyError`;
      unclaimed/expired → allow. **The lease IS the run-state.**
- [ ] **Plan EDIT granularity (DECIDED):** plan-DELETE checks the lease; plan-level
      *edits* rely on the goal/task guards (editing a *pending* goal while a *sibling*
      runs is ALLOWED — the guards protect the running parts). Don't block editing a
      pending goal just because a sibling runs.
- [ ] New errors: `PlanBusyError` (PLAN_BUSY), `TaskRunningError` (TASK_RUNNING).

### 3.6 Worker/scheduler health observability (distinct from the lease)
The lease is the recovery *mechanism*; health is *visibility* (you can't tell a hung
worker from an idle one, or spot a crash-loop, from the lease alone).
- [ ] Expose: per-worker **last heartbeat** (you already write these for the lease —
      health just SURFACES them), current claims, scheduler status, restart count → a
      **health endpoint + the telemetry stream**. Minimal but real.

**DoD:** worker on Redis, restartable, lease-recovers; launcher restarts a dead worker;
launcher OS-supervised + failure modes documented; interrupt/edit/rollback + mid-RUNNING
replan working end-to-end; mutation guards (task/goal status + plan-delete lease) with
clear errors; worker/scheduler health visible.

---

## Phase 4 — API, CLI, frontend, logging, telemetry

### 4.1 RESTful API (#45/#32)
- [ ] Routes → use cases: create / edit (`apply_edit`) / control (pause/resume) /
      resume_from_review / **`manual_retry`** / **`rebind_task_agent`** /
      **`request_replan`**; reference-data CRUD; two-tier config; **health**.
- [ ] **Centralized logging + error→HTTP mapping (#45)** — one mapping layer:
      - 404 → not-found (GOAL/TASK/MODEL/PROVIDER/CAPABILITY_NOT_FOUND, AGENT_NOT_FOUND)
      - 409 → conflict (STALE_VERSION, PLAN_BUSY, TASK_RUNNING, ENTITY_IN_USE,
        GOAL_ALREADY_RUNNING)
      - 422 → unprocessable (INVALID_EDIT, EMPTY_PLAN, CAPABILITY_NO_LONGER_SATISFIED)
      - 400 → malformed request
      - 500 → unexpected
      - 200 / 202 → ok / accepted (async-started work)
- [ ] **Pre-made-plan REST endpoint (#32)**: load an easily-verifiable pre-created plan
      (the e2e flows run against it — "like a simple REST server").

### 4.2 CLI remodel (#7)
- [ ] Reduce to **fundamental commands + `worker` init + `api` (FastAPI) init**. Strip
      the old CLI accretion. Config settable via CLI (#20).

### 4.3 Frontend — full workflow (#9)
- [ ] The complete cycle: project config → **DISCOVERY → ARCHITECTURE → ENRICHING →
      AWAITING_REVIEW → RUNNING → REVIEW → (REPLANNING → loop)**. All functional.
- [ ] Real-time **agent runtime logs** + **system event** feed surfaced.
- [ ] The **chatbox** drives DISCOVERY/REPLANNING dialogue and receives **planner logs**.

### 4.4 Logs + telemetry (#21/#22/#23/#46)
- [ ] **Three distinct streams, distinct sinks:**
      - **agent runtime logs** (live) → live feed (source: `agent_events`).
      - **system events** → live feed + telemetry (source: `outbox`).
      - **planner logs** → the **chatbox** (conversational; separate from the feed).
- [ ] **Centralized telemetry (#46)**: every event — error, action, request — into ONE
      telemetry system with simple persistence.
  - ⚠ **UNIFY, don't duplicate (decided)**: build on the existing `outbox` +
    `agent_events` (already the event backbone) + API request logs, written to a simple
    store (a SQLite `telemetry` table / append-only log). Dedup on `event_id`. NO second
    parallel event system.
- [ ] **Outbox RELAY (r2 — was unspecified):** something must actually deliver outbox
      rows to their consumers (dashboard SSE, telemetry). Simplest correct prototype: a
      small poller in the API process — `SELECT` undelivered outbox rows in id order →
      push to SSE + telemetry → mark delivered. At-least-once; consumers dedup on
      `event_id`. Without this item, events are written but never seen.

**DoD:** API with centralized logging + correct status mapping + health + pre-made-plan
endpoint; slimmed CLI; full frontend workflow (all 9 phases incl. the loop + chatbox);
three log streams correct; unified telemetry persisting all events; no secrets in
logs/telemetry.

---

## Phase 5 — Test architecture, CI, real e2e

Layered, systematic suite (#6/#44/#28/#29/#30/#31).

- [ ] **Unit** (domain + app, in-memory) — done (87, updated for the new phases); extend
      as adapters land.
- [ ] **Integration (#28)** — ≥1 per important flow step: config, **DISCOVERY,
      ARCHITECTURE, ENRICHING, AWAITING_REVIEW, RUNNING, REVIEW, REPLANNING/loop** —
      against real SQLite + dummy agent.
- [ ] **Regression** — lock every bug already fixed so it can't return: FAILED-loop,
      pending-goal-noise, backoff-survives-crash, stale-version, lease-reclaim,
      check-before-act idempotency.
- [ ] **Backend e2e (#29)** — ONE full-flow run (all phases incl. one loop iteration)
      with the **dummy agent**.
  - ⚠ **The dummy must be robust + imitate a real agent closely + be tested**: realistic
    event streams (tool calls, steps, tokens), realistic timing, structured artifacts,
    AND the **SHARED FAILURE TAXONOMY** (2.4) — emit rate_limit/token_limit/connection/
    auth/timeout/tool_error so dummy tests exercise the SAME retry/terminal paths
    production hits. Upgrade the current minimal dummy; **give it its OWN test suite.**
- [ ] **Frontend e2e (Playwright) (#31)** — ONE real full-cycle interaction (all cycles
      together) against **real infrastructure + a cheap real model**, driven by the
      pre-made plan (#32).
- [ ] **CI (git-flow) (#30)**:
      - **per-PR**: unit + integration + dummy-backend-e2e + **ruff**.
      - **merge-to-main / nightly**: the PAID real-model Playwright e2e (cost + flakiness
        → NOT per-PR). ⚠ This split is important — don't burn money/CI-time per push.

**DoD:** five test layers green; CI in git-flow with the right gates; dummy agent robust
+ tested + taxonomy-matched; one paid real-model Playwright e2e on the right cadence.

---

## Phase 6 — Final audit + launch campaign

Only after everything above.

- [ ] **Final code-smell + refactor audit** of the *integrated whole* (#43) — deliberately
      last, so you audit the assembled system, not pieces.
- [ ] **Systematic test refactor (#44)** into clean layers (unit / integration /
      regression / backend-e2e / frontend-e2e) — final organization pass.
- [ ] **Docs + decision/refactor history consolidated (#41)** — also campaign material.
- [ ] **Launch campaign (#47)**: ≥1 month, ≥3 posts/week, capabilities **and** the
      architectural reasoning + development process.
  - ⚠ Lead with the **engineering story** (decided): reconciler→pull-scan refactor,
    durable-backoff-gate reasoning, hexagonal discipline, crash-recovery proofs, the
    decision-gate whitespace, the append-only iteration loop. A 30-second dashboard demo
    GIF + the architecture narrative lands harder than feature lists. **Each ADR /
    decision-log entry is a post** — your decision history IS the content pipeline.

---

## ALL DECISIONS — quick reference

**Phase machine & loop:**
1. 9 phases: DISCOVERY, REPLANNING, ARCHITECTURE, ENRICHING, AWAITING_REVIEW, RUNNING,
   REVIEW, DONE, FAILED.
2. ENRICHING separate from ARCHITECTURE (crash-recovery granularity).
3. REPLANNING = conversational re-plan (= "refining"); reached from REVIEW and from
   mid-RUNNING chat. NOT manual editing.
4. `apply_edit` (surgical manual) ≠ `request_replan` (conversational) — separate use cases.
5. Loop is append-only goals + `iteration` counter; DONE goals are history + re-plan context.
6. Mid-RUNNING replan SKIPS incomplete goals (option a), generates fresh set.

**Concurrency / lease / recovery:**
7. Per-plan lease now (sequential); lease granularity = unit of parallelism (deferred to
   goal/task). Documented in the concurrency ADR.
8. Lease = recovery mechanism; health = visibility (kept as a small but distinct item).
9. Scheduler = thin, idempotent, OS-supervised; no distributed consensus.

**Retry / backoff:**
10. Automatic: domain decides (should_retry / backoff_for); durable `retry_not_before`
    gate (survives crashes); wait performed in orchestration, never a domain sleep.
11. Manual retry = clear gate + reset attempts to 0 + requeue, bypassing should_retry.
12. Shared failure taxonomy (connection/rate_limit/token_limit/auth/timeout/tool_error),
    used by BOTH real runner and dummy; token_limit/auth terminal, others retryable.

**Mutation safety:**
13. No global stop / SystemBusyError. Status/lease guards: task/goal not-RUNNING;
    plan-DELETE checks lease (PlanBusyError); plan-EDIT relies on goal/task guards.
14. rebind-on-edit: manual agent_id = override; requirements-edit = re-match.

**Infra / data:**
15. Old data thrown away (test only) — clean break, no migration.
16. Integration rollback = user-handled git branches.
17. Secrets reuse the existing encryption mechanism; never logged.
18. SQLite WAL; outbox transactional with state; telemetry unified on outbox/agent_events.
19. Outputs: zip + local-dir now; GitHub PR deferred. git-branching workspace = rollback.
20. Env provisioner (uv/Bun) + framework questionnaire: fields now, provisioning deferred.

**Workflow correctness (r2 review):**
21. Driver model: conversational phases (DISCOVERY/REPLANNING) = chat/API-driven;
    autonomous planning (ARCHITECTURE/ENRICHING) + RUNNING = worker-driven; gates =
    human-command-driven. Claim predicate = {ARCHITECTURE, ENRICHING, RUNNING}.
22. Gates ALWAYS pause; `pause_after` removed (obsolete — checkpoints are now phases).
23. RUNNING completion → REVIEW (not DONE); DONE only from REVIEW "finish".
24. Abandoned-iteration rule: skip PENDING at request_replan + finalize-abandon at
    REPLANNING completion; late failures terminal-skip (tolerant finalize), never
    requeue into an abandoned iteration. `Goal.skip()` added to the domain.
25. Worker tick reports *progress*, not *claiming* (no-progress → sleep) — kills the
    hot claim/release spin on backing-off plans.
26. Outbox relay = a small poller (API process) delivering outbox → SSE + telemetry,
    at-least-once, dedup on event_id.

**Deferred (cleanly shelved, designed seams):** PR gate, project spec, decision gate,
GitHub PR output, parallelism, env provisioner, Postgres.

---

## THE RULES ABOVE ALL OTHERS
- **🔒 Domain freeze after Phase 0** — mid-integration core churn is the top risk.
- **The real-SQLite re-run of the crash/backoff/outbox tests (2.1) is the integration's
  truth test** — it proves atomicity is real, not simulated.
- **Phase ordering is load-bearing** — the honest demo (and the campaign) needs the core
  actually running (Phases 2–3) before frontend polish or campaign.

## RISK REGISTER (easy to underestimate)
- Real transactional UoW atomicity (2.1) — subtle; the tests are the safety net.
- pi stdio contract (2.4) — verify vs the real pi build; isolate in one module.
- The phase-machine driver + REPLANNING loop wiring (2.5) — new application logic;
  the loop is the product's heart, build it carefully.
- Dummy-agent realism + taxonomy match (2.4 / 5) — a weak dummy makes the e2e meaningless.
- Scheduler crash semantics (3.2) — resist over-engineering; document instead.
