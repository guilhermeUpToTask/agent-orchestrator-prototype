# ROADMAP — first valuable public release

This roadmap is ordered by **external developer value and launch dependency**.
The orchestration architecture already exists; the release problem is making
one local workflow reproducible, understandable, trustworthy, and easy to
evaluate.

**Positioning:** a local-first, human-gated, verified multi-agent coding
orchestrator for developers working on their own repositories.

It is not a fully autonomous software factory, multi-tenant SaaS, fully
sandboxed platform, or replacement for an engineering team.

Status markers:

- ✅ Completed — verified in the repository
- 🚧 In progress — foundation exists; graduation work remains
- ⬜ Planned — required for the first peer preview
- ⏸ Deferred — reconsider only with run or user evidence

The launch sequence is:

```text
reproducible fixture
→ real-plan walkthroughs
→ backend stabilization
→ capability/API/frontend coverage matrix
→ API control-plane completion
→ frontend truth and operator UX
→ onboarding, packaging, documentation, and demo
→ in-product understanding
→ closing the demonstrability gaps
→ small peer preview
```

The last three were reordered on 2026-08-02: the preview used to come before
the gap-closing work. See Phase 7 for the reasoning and for what that reorder
costs.

Accepted ADRs and current code are authoritative. Domain changes require a
recorded unfreeze in [the decision log](docs/decisions/decision-log.md).
Verified unresolved defects belong in
[known issues](docs/architecture/known-issues.md), not duplicated here.

## Implemented launch foundation ✅

These are current capabilities, not future roadmap work:

- Cyclic long-lived project plans with intent, architecture, JIT enrichment,
  execution, publication, exact-revision review gates, and source-preserving
  replanning.
- Project-bound repository routing, worktree-isolated attempts, independent
  verification, task → goal → cycle Git promotion, and publication
  dispositions.
- SQLite version CAS, leases, per-goal claims, transactional outbox, operational
  ledgers, provider circuits, and SSE.
- Graceful pause, resume-only semantics, targeted retry, structured blocks,
  live-registry recovery, and provider-capacity waiting/admission/routing on
  per-`limit_scope` backoff curves.
- Automatic recovery that keeps a repairable mistake away from a human: the
  orchestrator's own rejection reasons are fed into the next agent attempt, a
  rejected candidate earns a bounded second try, an unsatisfiable contract is
  repaired in place (near-miss command paths, a test path the strategy requires),
  a transient goal merge is re-attempted, and a failed planning session leaves
  evidence the retry reuses. Every one is bounded, recorded, and still ends in a
  backstop block.
- Repository sight for the planner: bounded read-only tools over a committed ref
  (list / read / search / orientation), so contracts name paths that exist
  instead of being written blind, with submission-time rejection of a scope or
  command nothing could satisfy.
- Stub and OpenAI-compatible reasoners; dry-run and catalog-resolved real agent
  runtimes; provider/model/agent/capability/project catalogs.
- Plan, recovery, attempt, telemetry, config, readiness, and publication APIs,
  plus an operator frontend with gates and catalog settings.
- Dual fake/SQLite orchestration tests, Git/API/SSE integration tests, CI quality
  gates, a supervised dev launcher, release automation, and run-evidence export.
- Four operator fixtures, all API-only (`curl` + `jq`, no frontend):
  [`happy-path-v1`](fixtures/happy-path-v1/) (the locked one-goal walkthrough,
  Tier 0 and Tier 1), [`planning-recovery-v1`](fixtures/planning-recovery-v1/)
  (a starved planning session leaves evidence the retry can use),
  [`parallel-goals-v1`](fixtures/parallel-goals-v1/) (two goals promote into
  one cycle branch, so the second merge hits a base the first moved), and
  [`contract-repair-v1`](fixtures/contract-repair-v1/) (Tier 1: poison a frozen
  contract with a command that cannot pass, and prove it is repaired in place
  rather than escalated — the first fixture that drives a run which must FAIL
  first).
  Between them they found the repository-binding trap, an unhandled
  `RoleUnsatisfiableError` that crash-looped the worker, a contract whose
  strategy contradicted its own scope, capacity failures spending the
  verification retry ceiling, and the contract-repair write that deadlocked
  SQLite against the transaction that called it.

Completed foundations stay in architecture docs and tests. They are not
reintroduced below merely because further hardening is possible.

## Phase 0 — reproducible validation baseline ✅

**External capability:** a developer can run one free, deterministic walkthrough
that proves the lifecycle, gates, worker, Git/verification path, publication,
API, and basic UI wiring against the same disposable repository every time.

### Deliverables

- ✅ Keep `happy-path-v1` as the canonical one-goal, one-function fixture,
  materialized outside this repository.
- ✅ Reuse the canonical cyclic integration test and existing run exporters;
  do not create a second orchestration model inside the fixture.
- ✅ Correct repository binding. The walkthrough told operators to export
  `PROJECT_REPO_DIR`, which `AppContainer` does not read: a project with no
  `repo_url` gets a fresh empty repo auto-seeded at
  `$ORCHESTRATOR_HOME/projects/<id>/repo`, so a run "passed" against a tree the
  checker never looked at. Found on the first live run of this fixture; the
  README now creates the project with `repo_url` set and states the trap.
- ✅ The materialize → bind → run → publish → check → reset sequence is
  deterministic from a clean state, verified by repeated Tier 0 runs.
- ✅ A precise command contract, not a driver: the walkthrough is API-only
  (`scripts/api.sh` + the exact `curl`/`jq` calls), so no step depends on
  reading a screen. Review gates stay explicit operator actions.
- ✅ A companion checker validates what `pytest` cannot: `scripts/verify_run.py`
  asserts cycle activation (the durable proof both gates were approved — the
  aggregate keeps no gate history), the goal/task size budget, tasks DONE with
  **accepted, revision-bound** evidence, goals promoted, no open plan-wide or
  per-goal block, disposition recorded with an output reference, root back to
  IDLE, and the Git chain: cycle branch descends from the seed tag, goal branches
  merged into it, default branch byte-identical to the seed, repository isolated
  from the orchestrator checkout. Tier 1 adds `check-success.sh` as expectation 7.
  Exit 1 is a failed check, exit 2 a broken harness — the two are different
  findings and must not share a code.
- ✅ `scripts/capture-run.sh` writes one run directory per run
  (`runs/<UTC>-tier<N>-<plan prefix>/`): manifest (fixture version, seed commit,
  orchestrator SHA + dirty flag, pinned reasoner/runner/agent bindings, failed
  checks), verification result, plan snapshot, evidence bundle, attempt timeline,
  telemetry, planning artifacts, and the worker-log reference. It captures a red
  run too — that evidence is the point.
- ✅ `backend/tests/integration/test_happy_path_fixture.py` locks the seed
  (starts RED, exact promised assertion), the brief (postable verbatim, names the
  verification command and the size budget), and the checker's judgement, without
  driving the lifecycle the cyclic suite already owns.
- ✅ Version the fixture: the locked brief lives in `brief.txt` so it can be
  posted verbatim (`BRIEF.md` had prose that would have been sent to the
  reasoner as part of the brief). The v1/v2 rule is documented. Still v1 — the
  Phase 0 work changed tooling only; the seed, the brief, and the eight
  expectations are untouched, so no recorded run series is invalidated.

### Exit criteria

- ✅ A clean Tier 0 run repeats after `reset.sh` with the same result and no
  network/API key. Verified 2026-07-27: two consecutive stub + dry-run walkthroughs
  (intent → draft → enrichment → execution → publication), 16/16 checks green
  each, one goal and one task per cycle, default branch untouched.
- ✅ The checker fails on the seed and passes only on the promoted result. Tier 1
  against the same dry-run output fails on expectation 7 alone
  (`NotImplementedError`), which is the seed code promoted unmodified — the
  checker cannot be satisfied by promotion without implementation.
- ✅ Artifacts identify fixture/code version, plan/cycle, timeline, evidence,
  disposition, and Git refs (`capture-run.sh` manifest + bundle).
- ✅ The real API and worker are exercised. This fixture is **API-only by
  design** (`curl` + `jq`), so it carries no UI criterion: frontend coverage is
  Phase 5's job, against Phase 3's matrix.

### Fixed by the Tier 0 pass

Three defects, all found by running the walkthrough rather than reading it:

- `dev.sh seed` before `dev.sh start` died with a raw
  `OperationalError: no such table: capabilities`. `start` migrated and `seed`
  did not, so the documented order for a fresh state directory failed on the
  first command. `seed` now migrates too.
- Re-running against the same project does **not** create a second plan: a
  `ProjectDefinition` owns exactly one long-lived `Plan` (ADR-003), so re-POSTing
  the brief returns the existing `plan_id` and opens cycle *n+1* on it. The
  fixture README said "or a new plan under the SAME project", which cannot
  happen; corrected, and `verify_run.py` grew `--cycle-id` because "the latest
  completed cycle" is the right default only for the run just finished.
- `reset.sh` reset `main` and the working tree but left every
  `plan/`/`cycle/`/`goal/`/`task/` branch behind, so run *N* started carrying
  ~3(*N*-1) stale branches and the runs stopped being comparable. It now deletes
  that hierarchy — matching by ref prefix, since a task branch is
  `task/<id>/a<attempt>` and `refs/heads/task/*` silently misses two levels.
- `reset.sh` reset only GIT. The orchestrator database was untouched, so every
  re-run added another cycle to the same long-lived plan and no two runs started
  from the same state — the fixture's core promise. Fixing it needed a supported
  way to dispose of a plan, so **`DELETE /api/plans/{id}` landed early from
  Phase 4** (`delete_plan`; 409 `PLAN_BUSY` while a worker holds the lease), and
  **migration 0015** gave every plan-scoped table `ON DELETE CASCADE` so one
  delete leaves no orphan. Two tables had a foreign key with no `ON DELETE` (the
  delete was rejected) and two carried a `plan_id` with no foreign key at all
  (rows were silently orphaned). `reset.sh` now deletes the fixture's plans
  through the API — best-effort, so a reset still works with the API down, and
  scoped to plans bound to this fixture's project. Verified: run, reset, re-run
  yields `created=true`, a new plan id, exactly one cycle, and zero rows left in
  any plan-scoped table while the seeded catalog survives.

## Phase 1 — Tier 1 real-runtime happy path ✅

**External capability:** a developer can evaluate whether one pinned real
reasoner and coding runtime reliably complete a tiny, verifiable change.

### Deliverables

- Use `happy-path-v1` unchanged: locked greeting brief, one goal preferred, one
  or two tasks, and `python -m pytest -q`.
- Pin and record the reasoner provider/model, agent runtime and provider/model,
  timeout, concurrency, and orchestrator version.
- Preflight reasoner, agent binding, binary, secret, repository, and verification
  command readiness before spending on a run.
- Reset the repository and cycle state between comparable runs. Never repair the
  greeter manually during a measured run.
- Export the snapshot and evidence bundle for every green or red run.
- Classify failures as product, setup/config, provider capacity, model quality,
  or fixture defects.

### Result (2026-07-27)

**Four green Tier 1 runs, 17/17 each, expectation 7 included — three of them
consecutive on an identical pin with no code change between them.** Three ran
entirely on free OpenRouter models at **$0**; the first used
`anthropic/claude-haiku-4.5` for the coding agent (under $0.10) because the free
endpoint was saturated at the time. Fixture `happy-path-v2`, reasoner
`nvidia/nemotron-3-ultra-550b-a55b:free`, `pi` runtime.

Each run: the agent authored `tests/test_greeter.py`, the orchestrator recorded a
**RED** baseline, the implementer turned it GREEN, and the out-of-repo acceptance
check confirmed the authored test **discriminates** — it fails against a
deliberately broken `greet`.

Getting there took a fixture change and a pipeline redesign, both from run
evidence:

- **The pipeline had one shape.** `_run_role_for` always ran an authoring stage
  and never read `verification_strategy`, so a repo that already contained a
  failing test had no way through: two runs died on
  `test author produced no executable checks` and
  `did not establish a passing characterization/check baseline`. The reasoner's
  contract was correct both times.
- **A task's checks are identified by declaration** (`agent_orchestrator/app/test_identity.py`),
  from the author's diff or a `verification_command` naming a concrete file —
  never a repository scan, which cannot tell task 3's checks from task 1's. When
  the contract names a check that is already present, no agent runs at all.
- **Checks a task did not write are protected**, at both stages, via a snapshot
  taken before the author runs. That closed a real hole: task 2's author could
  rewrite task 1's check into something trivially failing, have it hashed as task
  2's evidence, and let the implementer "fix" it.
- **The fixture's verdict was circular.** v1 ran `pytest` inside the repo, in the
  same `tests/` the agent writes to. v2 ships `tests/` empty and holds the
  acceptance check outside the repo, with a mutation probe that catches a vacuous
  test — something v1 could not detect at all.
- **Tier 0's verification proved nothing** until now: the stub used
  `git diff --check`, which cannot fail on a clean worktree. It now names a path
  that genuinely goes RED→GREEN, and the recorded baseline shows exit code 1.

**Free-tier contention makes a run slow, not unreliable.**
`ResourceExhausted: Worker local total request limit reached (32/32)` is Nvidia's
SHARED worker pool across every user of that free endpoint — six concurrent probes
from one client succeed, so it is global load, not a client-side cap. One run
needed six attempts across 37 minutes of capacity backoff and then completed
green. The orchestrator classified every one correctly:
`limit_scope: request_concurrency` opened **no** circuit and left
`provider_waiting` null, requeueing that one task exactly as designed, with no
false block and no other work stalled. Also observed:
`anthropic/claude-3.5-haiku` is not a valid OpenRouter slug (404 → clean
terminal block).

### Exit criteria

- ✅ Three consecutive real runs complete with no unexpected human code
  correction. Four green in total, three consecutive on an identical pin. One
  further run was abandoned mid-backoff during cleanup rather than by any
  failure — the run after it proved that path completes given time.
- No false terminal block or hot loop occurs.
- Every accepted task has correct revision-bound verification evidence.
- Work promotes through expected Git refs while the seed default branch remains
  unchanged.
- Publication records the disposition and returns the project plan to `idle`.
- Evidence, including usage when reported, is comparable across all three runs.

## Phase 2 — walkthrough-driven backend hardening ✅

**External capability:** the tiny workflow survives common failures and either
recovers automatically or tells the operator exactly what to do.

The recovery architecture exists. Validate it with Tier 0/Tier 1 evidence
instead of redesigning it speculatively.

**Closed 2026-07-28.** Six defects found and fixed, every one reproduced before
it was touched and locked on both the fake and SQLite; one domain un-freeze
(#18); one new fixture. The pattern worth keeping: a single RED Tier 1 run found
three defects that four green runs had missed, because all three need a failing
agent to exist at all. Green runs prove the happy path; only a failing one
exercises recovery.

### Fixed — capacity backoff ignored `limit_scope`

Found by the Phase 1 series. `kind_backoff_scale` applied `{rate_limit: 4.0}` to
every rate-limited attempt regardless of `limit_scope`, so a
`request_concurrency` refusal escalated on the same curve as an account-level
quota exhaustion: 2min, 4min, 8min, then capped at 4x `max_backoff_seconds` —
the scale multiplies the ceiling as well as the base delay. But those two mean
opposite things. A quota is exhausted and deserves a long wait; a concurrency
refusal on a SHARED pool means "someone else is using it right now", and is the
case the design already singles out as opening no circuit and requeueing just
that task.

Measured: one Tier 1 run spent 37 minutes in backoff over six attempts against a
free endpoint that answered six concurrent probes instantly between them. The run
completed green, so this cost wall-clock rather than correctness — but it backed
off hardest exactly when a short retry would most likely succeed.

Fixed by a per-scope curve in `agent_orchestrator/app/provider_capacity.py`
(`capacity_backoff_seconds`): a positively identified `request_concurrency`
refusal waits the plan's ordinary configured curve, unscaled, and every other
scope — including an unclassified one, which degrades to patience for the same
reason it degrades to the narrower circuit key — keeps 4.0. No domain change:
`RetryPolicy` already carries the scale as configuration, and the app layer that
classifies the failure hands it a scope-adjusted copy. Regression tests cover the
policy directly and the armed gate through `ExecutionHandler` on both the fake
and SQLite backends.

### Fixed — a crashing plan starved every healthy plan

Exit criterion 2 ("repeated unexpected worker exceptions cannot starve healthy
plans") did not hold. It was a *known* risk — `known-issues.md` recorded it
under Operational visibility ("a malformed plan that raises before any save can
still be reclaimed first by oldest `updated_at`") — but nothing had reproduced
it, and `execution-model.md` pointed at it by a label, "H2", that the entry
never carried, so the cross-reference resolved to nothing.

Reproduced at the truth-test level on both backends: two RUNNING plans, a worker
whose tick throws on the first. Five polls claimed `['poisoned'] * 5` — the
healthy plan was never claimed once.

`_CLAIM_SQL` selected `ORDER BY updated_at LIMIT 1`, and neither the claim nor
the release touches `updated_at` — only `plans.save` does. A tick that throws
never reaches a save, so the poisoned plan's `updated_at` never moved, it stayed
the oldest row, and every subsequent poll re-selected it. The worker itself
survived exactly as designed (`worker.tick_failed` logs, releases, backs off one
poll), which is why this never showed up as a crash: the plan-level claim slot
was simply monopolized, and every other plan's planning turns, gates, and
enrichment silently stopped. Goal-level execution was unaffected — it holds the
separate `claim_ready_goal` lease.

Fixed by making the claim round-robin on `claimed_at`: the claim stamps it, the
release no longer clears it, and the order is
`COALESCE(claimed_at, 0) ASC, updated_at ASC`. No migration — the column already
existed and was written but never read anywhere, so it was free to become the
fairness cursor; `claimed_by` remains the sole authority on "currently held". A
never-claimed plan sorts first, so new work is not made to wait, and a poisoned
plan is never quarantined (it may recover) — it just takes its turn. The
in-memory fake starved for its own reason (first claimable plan in insertion
order, no fairness at all) and now mirrors the same cursor, per the
fake/real-parity invariant.

### Experiment results (2026-07-27, API-only operator session)

Driven through the exposed API as the operator, Tier 0 unless noted. Findings
that produced fixes are the two "Fixed" sections above; everything else is
recorded here because a scenario that finds nothing is also a result.

**Provider capacity — swept, no new defects.** All nine listed scenarios already
have automated coverage on both backends (connection failure, rate limit, daily
quota, request concurrency, admission, circuit scope, half-open probe, alternate
routing, wall-clock ceilings). The remaining work in this category is Tier 1
evidence, not more tests. A Tier 1 happy-path run went 17/17 green and hit **zero**
capacity events, so it confirms no regression rather than confirming the backoff
fix — free-tier refusals are not summonable on demand, and the original 37-minute
measurement was opportunistic rather than a controlled experiment.

### Fixed — the Tier 1 series (three defects the green runs could not reach)

A three-run Tier 1 series against the free models found more in one red run than
four green ones had. All three need a *failing agent* to appear, which is why
Tier 0 and a green Tier 1 both miss them.

**1. `contract_repair` self-deadlocked SQLite and could never persist.**
`PlanningArtifactStore` deliberately writes on its own short transaction so the
record survives a plan rollback — which means a second connection, and
SQLite/WAL allows exactly one writer. But `_repair_contract` ran *inside* the
finalize transaction (`execution_handler` `with uow:`), so the plan connection
held the write lock while synchronously waiting for the artifact connection to
get it. `busy_timeout` and the `run_in_session` retry budget cannot break that —
the caller is the holder. Live: five consecutive attempts died
`InfrastructureError: Database stayed locked beyond retry budget` out of
`drive_goal`, each abandoning its attempt and losing the repair, so the
*identical* repair was recomputed every time until the retry budget ran out and
the goal blocked. The machinery that exists to keep a repairable contract away
from a human was itself what blocked the human in. Reproduced in a 30-line
integration test with no agents or concurrency, then fixed by queueing the write
and flushing it once the transaction closes (`_flush_pending_artifacts`, in a
`finally` — the record matters most when finalize did *not* complete).

**2. One exception in the goal-claim scan killed the worker process.** The scan
sits after `worker_tick`'s try/except and had no guard of its own. A plan
deleted between the readiness scan and the lease INSERT raised
`FOREIGN KEY constraint failed` out of `claim_ready_goal`, unwound the loop, and
exited the process 1 — under the dev supervisor that took the API down with it.
A delete racing a claim is ordinary (`DELETE /api/plans/{id}` cascades while a
scan is in flight), so it has to be survivable. This is exit criterion 2 in its
starkest form: not starvation, termination.

**3. A concurrency refusal still spends the per-task retry budget.** This is the
half of the original `limit_scope` defect that the backoff fix did not touch,
and the series is the first evidence of it. `capacity_wait` is set only when a
circuit opens, and a `REQUEST_CONCURRENCY` refusal deliberately opens none — so
unlike every other capacity failure it does *not* bypass the budget. Run 1
ended `execution_failure` after 7 attempts with the last one
`rate_limit/request_concurrency`; run 2 showed the same shape. A busy shared
pool can therefore block a goal that has nothing wrong with it. Left unfixed on
purpose: the fix is to make concurrency refusals budget-neutral inside a
wall-clock bound, which is a capacity-policy change deserving its own decision
rather than a drive-by. Note the interaction — the faster curve reaches the
budget sooner in wall-clock terms, so this got *more* visible, not less.

### Validated — a Tier 1 run that absorbed real concurrency refusals

The evidence the backoff fix was missing, from a clean run with all fixes in
place (`runs/20260727T223637Z-tier1-ae16ab2a`, **17/17 green** including pytest
passing on a real checkout of the cycle branch):

| attempt | outcome | gap before it |
|---|---|---|
| 1 | succeeded (test author) | — |
| 2 | `rate_limit` / `request_concurrency` | — |
| 3 | `rate_limit` / `request_concurrency` | **58s** |
| 4 | succeeded (implementation) | **121s** |

58s then 121s is the plan's ordinary configured curve — a 30s base doubling,
jittered. Under the 4.0 rate-limit scale those same two waits would have been
roughly 232s and 484s: ~12 minutes of waiting instead of ~3, for refusals the
provider cleared in under a minute. That is the 37-minute measurement in
miniature, and it no longer happens.

The more important half: **the task recovered.** Two refusals were absorbed and
the goal completed, where the pre-fix series runs exhausted the budget and
blocked. Zero `Database stayed locked` events (seven in the pre-fix session) and
the worker survived the whole run.

Not exercised in that run: `contract_repair` never fired, because the agent
succeeded. **Closed since, by [`contract-repair-v1`](fixtures/contract-repair-v1/)**
— a fixture that poisons a frozen contract on purpose so the repair path is
reachable on demand instead of by luck. Green end to end: the poisoned command
produced a contract-shaped failure, the repair was recorded `committed`, the
contract was snapped to the real path, the task reached DONE with no block, and
**zero** `Database stayed locked` events. The deadlock fix is now validated
live, not merely by its regression test.

One property of that fixture is itself a finding: the operator window it needs
is a **race**. `update_task_contract` requires the task PENDING (or FAILED while
paused), enrichment and the first attempt run under one claim, and the pause has
to settle on a TDD stage boundary. It won two runs of four; the losses exit 2
(SETUP), never a false FAIL. That is the deferred "no operator control point at
the contract boundary" item priced in a concrete unit: a free deterministic
regression test versus a paid one that needs to win a race.

**Controls — one defect, otherwise correct.**
- Pausing a plan parked at a review gate is refused (422 `INVALID_TRANSITION`),
  and `legal_actions` correctly does not advertise `pause` there. Refusing what
  you never offered is the right shape.
- Pause during execution settles PAUSED within ~2s, and `legal_actions` becomes
  `[resume, start_replan, edit_pending_work]`.
- Resume restores availability only, and the plan runs to publication.
- **Defect: refusal messages mix two vocabularies.** `request_pause` reports
  `status.value` (cyclic: "cannot transition from **waiting** to paused") while
  `pause` and `resume` report `phase.value` (legacy nine-phase: "cannot
  transition from **discovery** to resumed") — `planner_orchestrator.py:395` vs
  `:419`/`:427`. A cyclic plan the API describes as `status: waiting, reason:
  intent` is refused in nine-phase words, which is exactly the vocabulary the
  cyclic model replaced. One-token fix each, but it edits the FROZEN aggregate,
  so it is recorded rather than applied.
- **Not reachable at Tier 0:** targeted retry and block resolution both need a
  FAILED task, and the dry-run runner always succeeds. Their controls are
  covered at the orchestration level instead. An API-only walkthrough cannot
  exercise the failure-path controls without a fault-injection seam.

**Recovery — one defect fixed; the latency itself is by design.**

The mid-attempt crash experiment turned up a real bug on the way past:
`reconcile_stale_attempts` gated only on `plans.is_claim_live`, but since goal
leases (un-freeze #13) attempts are created by goal workers, which do not hold
the plan claim while they run. A second worker's STARTUP reconciliation
therefore saw a RUNNING attempt with no live plan claim and abandoned a ledger
row whose process was alive and about to finalize it. Single-worker restart
never exposed it — there the old process really is dead — so it took two
workers to surface, which is how it survived. Now checks both leases; locked by
`test_startup_reconciliation_respects_a_live_GOAL_lease` on both backends.

The ~6 minute recovery latency around it is **not** a bug:

- Startup reconciliation deliberately does not revert the domain task. It
  closes the ledger row and stops, precisely so a dead process is never
  mistaken for a task outcome. That restraint is load-bearing.
- The goal lease expiring is therefore the only correct recovery trigger, and
  it is the designed one: "a dead worker's lease expires and any worker
  reclaims from persisted state". A restarting worker has no other liveness
  signal about the previous holder.
- So the wait equals `lease_seconds` (300s default for goals) plus poll
  cadence, which is what was measured. Working as intended.

If it is ever worth shortening, in increasing order of cost:

1. **Lower the goal `lease_seconds`.** Pure configuration, and cheaper than it
   looks: the lease does NOT have to cover a whole attempt, because an active
   action renews it every `lease_seconds / 3`. That is why the 300s default
   already sits below `agent_runner.timeout_seconds` (600) without a live
   worker ever losing its goal. The real floor is the worst-case gap between
   heartbeats — a renewal must land before expiry under scheduling jitter, GC
   pauses, and a loaded host — so the tunable quantity is that safety margin,
   not the attempt duration. Recovery latency falls linearly with it.
2. **Have a worker release its goal leases on graceful shutdown.** Turns an
   orderly restart into instant recovery and leaves only `kill -9` paying the
   full lease. Does nothing for a hard crash, which is the case that matters.
3. **A liveness registry** (worker heartbeat rows, reclaim when the *worker* is
   dead rather than when the lease expires). Correct and fast, and the most
   coordination infrastructure — the phase's own rule says not to add that
   without run evidence, and one crash test is not it.

The recommendation is (1) after checking it against the runner timeout, and
otherwise to leave the latency alone and fix the *visibility*, below — an
operator who can see "lease expires in 4m12s, last heartbeat 90s ago" does not
experience a correct 6-minute wait as a hang.

**Recovery — the remaining gap is visibility, not correctness.**
- `kill -9` the worker *before* any attempt starts: a restarted worker reclaims
  from persisted state and reaches publication in ~8s. Nothing was invented.
- `kill -9` the worker *mid-attempt*, with attempt 1 RUNNING: startup
  reconciliation closes the ledger row to `abandoned` without inventing an
  outcome (correct), and the plan does eventually recover — attempts 2 and 3 run,
  the task reaches DONE, publication opens.
- **Measured recovery latency: ~6 minutes**, bounded by the 300s goal lease, not
  by the attempt's death. Reconciliation deliberately does not revert the domain
  task, so nothing shortens the wait.
- **The whole window is invisible.** Throughout it the plan reports
  `status: running` with its task `running` and `retry_not_before: null` —
  identical to genuine work. This is run evidence for the standing
  "Operational visibility" known issue: the read model exposes the active run
  start but neither the lease deadline nor the last heartbeat, so an operator
  cannot tell a live attempt from a dead worker's orphan.

**Git and scheduling — covered by existing runs.** happy-path Tier 0/Tier 1 and
parallel-goals-v1 together exercise failed-attempt cleanup, the protected default
branch (`main` still at the seed tag every run), task→goal→cycle promotion, two
independent goals promoting into one cycle branch, and publication output.
Startup worktree audit ran clean on every restart.

**Planning quality — not attempted.** Goal/task fan-out, duplicate test-only
work, capability coverage, model-role adherence, and pre-execution cost
visibility all need Tier 1 volume to judge, and one green run is not a sample.

### Priority experiments

- Provider capacity: connection failure, rate limit, daily quota, request
  concurrency, admission, circuit scope, half-open probe, alternate routing,
  and wall-clock ceilings.
- Controls: pause during work, graceful stop, resume-only behavior, targeted and
  planning retry, task edit, registry repair, block resolution, and replan.
- Recovery: process crash, expired plan/goal lease, heartbeat/liveness, CAS
  finalize race, late result, and malformed-plan starvation.
- Git: failed-attempt cleanup, stale worktree recovery, protected default branch,
  promotion reservations, concurrent goal merge, and publication output.
- Planning quality: goal/task fan-out, duplicate test-only work, capability
  coverage, model-role adherence, and pre-execution cost visibility.
- Scheduling: dependencies, independent-goal progress, per-goal blocks, and
  provider-capacity interaction.

Every verified defect follows:

```text
reproduction
→ minimal fix
→ regression test against fake and/or SQLite truth
→ Tier 0 rerun
→ Tier 1 rerun when execution, reasoning, verification, runtime resolution,
  capacity, workspace, or publication changed
```

Do not expand the domain or introduce coordination infrastructure without run
evidence and a decision-log entry.

### Exit criteria — met 2026-07-28

- ✅ **No launch-critical defect can hot-loop, corrupt state, promote unverified
  work, touch the default branch, or advertise an unusable recovery.** Six
  defects found and fixed this phase, each reproduced first and locked on both
  backends. `main` stayed at the seed tag in every one of six fixture runs;
  `block_policy` keeps advertised resolutions honest; the one remaining capacity
  gap is deferred below and ends in a recoverable block, not corruption.
- ✅ **Repeated unexpected worker exceptions cannot starve healthy plans.** Two
  distinct failures fixed: the claim is round-robin so a crashing plan cannot
  monopolize it, and the goal-claim scan no longer terminates the process. Both
  locked on the fake and SQLite.
- ✅ **Operators can distinguish active work, capacity/backoff waiting, graceful
  pause, recoverable block, and external terminal failure from persisted
  facts.** The last gap was liveness — a dead worker's orphan read as active
  work for the whole lease. Plan detail now serves `worker_lease` (scope,
  holder, heartbeat deadline, `expired`, `seconds_remaining`) beside
  `active_run`, which only ever said when work *began*.
- ✅ **Relevant regressions pass the baseline fixture contract.** happy-path-v1
  Tier 0 ×3 and Tier 1 ×2 (17/17 including pytest green on a real checkout),
  parallel-goals-v1, and contract-repair-v1 all green.

### Deferred out of Phase 2 — every one recorded in known-issues

Five issues were found while stabilizing execution and deliberately not fixed
in this phase. None was a correctness hole; each is a decision or a missing
control, and holding the phase open for any of them would have been holding it
open for a preference. All five are written up with their mechanism, their
evidence, and the options for whoever picks them up, in
[`docs/architecture/known-issues.md`](docs/architecture/known-issues.md).
**One (#4) has since been fixed; four remain open by choice:**

1. **A `request_concurrency` refusal spends the per-task retry budget.**
   *Revisit after launch — carried to Phase 8.* Alone among capacity failures
   it does not bypass the budget, because a concurrency refusal deliberately
   opens no circuit and so records no `opened_at` for a wall-clock bound to
   measure. It ends in a recoverable block advertising `retry_stage`, so the
   operator has a move.
2. **Goal promotion cannot recover from a cycle branch that MOVED.** The
   transient half is handled (classified fail-closed, reservation released,
   bounded re-attempt); a moved base needs rebase plus goal-level
   re-verification, which is Phase 8 work waiting on run evidence.
3. **No operator control point at the contract boundary** — nothing outside the
   process can hold a plan between "the contract is frozen" and "an agent is
   running against it". Priced concretely by `contract-repair-v1`, which must
   win a race for that window. Still a review-gate-like opt-in hold, still not
   worth a general "pause between units".
4. ~~**Reasoner config is boot-time only**~~ — **fixed 2026-08-01.**
   `AppContainer.reasoner` returns a `LiveReasoner` that re-resolves on every
   call, so a `reasoner.*` write lands on the next planning session instead of
   the next worker restart. Uncaching alone would not have done it: the worker
   captures the instance into `PlanningHandler` at boot.
5. **`planning_artifacts` grows without bound.** Reads stay bounded and a plan
   delete cascades, so this is storage cost only — and explicitly not a reason
   to invent a background sweeper.

Also standing, both in known-issues and neither Phase 2 scope: no
dead-letter/quarantine for a plan that fails forever (it now takes a fair share
rather than starving others), and one-second claim-fairness granularity.

## Phase 3 — capability-to-product coverage audit ✅

**External capability:** maintainers can state exactly which operator workflows
are supported and where each capability is exposed.

Create one authoritative matrix:

```text
domain capability
→ application use case/handler
→ FastAPI route
→ frontend consumer
→ tests
→ status
→ launch priority
```

Classify rows as implemented/exposed, implemented but hidden, exposed but
unused, frontend complete/partial/contradictory, legacy-only, and
launch-critical/deferred.

Audit setup, readiness, planning/gates, execution visibility, capacity,
recovery, evidence, repository/Git output, publication, diagnostics, and health.
Include backend routes not consumed by the UI, such as live attempt-log SSE.

### Result (2026-07-28)

The matrix lives at
[`docs/architecture/capability-matrix.md`](docs/architecture/capability-matrix.md)
and covers all **64 served operations** plus the capabilities that reach no
route at all, classified across the eleven audit areas and anchored to the nine
operator jobs the walkthrough fixtures actually execute (J1 install → J9
intervene).

It is machine-locked in both directions by
`backend/tests/unit/test_capability_matrix.py`: a route the app serves but the
matrix does not classify fails the build, and so does a matrix row naming a
route that no longer exists. The frontend and tests columns cannot be
machine-checked and are re-audited when a phase closes.

**Twelve gaps**, each verified by reading the code, each given an owner phase
and an objective test (G1–G12 in the matrix). What the audit changed about the
plan:

- Five gaps went to **Phase 4**, the largest being that the token guard covers
  28 of 64 operations — the whole plan lifecycle and the event stream are
  unauthenticated even when `ORCHESTRATOR_API_TOKEN` is set, contradicting
  `security.py`'s own docstring.
- Six went to **Phase 5**. At audit time, two were backend capabilities nothing
  rendered: per-goal blocks (un-freeze #14) and `provider_waiting`. Phase 5 has
  since closed all six and the matrix was re-audited on 2026-08-01.
- One (skip/abandon a wedged task) went to **Phase 8**: retry, edit and replan
  have covered every case the walkthroughs produced so far.

Two defects surfaced: the settings forms silently cleared provider/model
capacity overrides on every save (closed by Phase 5), and
`POST /api/plans/{plan_id}/retry-policy` had no route test (closed by Phase 4).
One drift was fixed in place — the hand-declared frontend read model omitted
`provider_waiting` and `legacy_phase`, now locked by
`backend/tests/unit/test_plan_read_model_parity.py`.

No endpoint was proposed for symmetry. Four candidates were explicitly recorded
as **non-gaps** so a later audit does not re-propose them: a generic
`resolve_block` route, operator control points inside the automatic recovery
loops, nine-phase transitions for cyclic plans, and forge/PR writes.

### Exit criteria — met 2026-07-28

- ✅ No endpoint is proposed merely for completeness; every launch-critical row
  maps to a walkthrough operator job (J1–J9, cited per section).
- ✅ Nine-phase fields/routes are labelled compatibility-only (matrix §9: four
  routes, plus `phase`, `legacy_phase`, `iteration` and root `goals`).
- ✅ Each launch-critical gap has an owner phase and an objective test. Eleven
  landed in Phases 4 and 5; none needed Phase 6, and the single deferral (G12)
  is owned by Phase 8 with the evidence that would promote it stated.

## Phase 4 — API control-plane completion ✅

**External capability:** a technical operator can set up, drive, inspect,
recover, and publish the happy path without database surgery.

Close only launch-critical matrix gaps. The audit assigned five here, each with
its objective test stated in the matrix:

- **G1 — extend the control-plane token guard to the plan lifecycle and
  `GET /api/events`.** `require_api_token` is declared on the `reference`,
  `config`, `reasoner`, `runner` and `metrics` routers only, so 36 of 64
  operations — every gate approval, `POST …/publication`,
  `DELETE /api/plans/{plan_id}`, and the full plan document with its brief and
  chat — are open to anyone who can reach the port while
  `security.py` states the opposite. Parametrize the guard test over the whole
  mutating surface, not one GET.
- **G9 — one evidence read model per cycle**: accepted evidence refs, protected
  paths, promoted refs (today reconstructed from the `cycle/<id>` convention by
  `verify_run.py`) and the recorded disposition. Today all of it is reachable
  only by reading the whole plan document, or by a CLI export script.
- **G10 — a worker-health read.** `worker_lease` answers "is this plan claimed"
  and only while it is; nothing answers "is a worker running at all", which is
  the most common local-setup failure and a J2 checklist item.
- **G11 — validate repository binding, and never initialize a named repository.**
  `POST`/`PUT /api/projects` store `repo_url` unchecked, and there is no late
  failure to catch: a path with no `.git` reports default branch `main`
  (`project_workspace.py:66`) and is then created, `git init`ed and given an
  empty initial commit (`workspace.py:150-153`). A mistyped `repo_url` runs to a
  green publication against an empty repository, so write-time validation alone
  does not close it.
- **G6 — a route contract test for `POST /api/plans/{plan_id}/retry-policy`**,
  the only route in the app no test exercises.

Then investigate:

- project/repository binding, local/remote validation, and workspace readiness;
- canonical legal actions for intent, gates, pause/resume, retry/edit, block
  resolution, replan, and publication;
- reasoner, agent, provider/model, capability, binary, sandbox, and secret
  readiness without exposing secret material;
- active action, attempts, liveness/deadlines, live logs, capacity, and backoff;
- verification evidence, protected scope, promoted refs, disposition/output,
  and evidence-export guidance;
- API/worker health and diagnostics needed by the setup checklist.

Keep routers thin. Preserve error mapping, UoW/CAS, outbox, side-effect
boundaries, and the dependency rule.

### Exit criteria

- Tier 0/Tier 1 need no direct SQLite edit or hidden env fallback.
- Every advertised action works in the state that advertises it.
- OpenAPI describes the cyclic lifecycle and launch-critical models; generated
  frontend types are current.
- Integration tests cover each new/corrected contract.

### Delivery status (updated 2026-08-01)

- **P4.1 — access and setup truth** (G1, G6, G11): ✅ merged, `fc54a41` (#63).
- **P4.2 — operational truth** (G10, `requires_human`, `action_endpoints`,
  domain un-freeze #19): ✅ merged, `52d5875` (#64).
- **P4.3 — evidence truth** (G9): ✅ complete; the
  `phase-4-3-evidence-truth` delivery closes Phase 4.
  - Design: `docs/superpowers/specs/2026-07-28-phase-4-3-evidence-truth-design.md`
  - Plan: `docs/superpowers/plans/2026-07-28-phase-4-3-evidence-truth.md`
    (9 tasks; execution ledger at
    `.superpowers/sdd/2026-07-28-phase-4-3-evidence-truth/progress.md`)
  - **Complete:** branch-naming module (`agent_orchestrator/app/branch_names.py`), migration
    `0017_goal_promotions`, the transactional promotion ledger as a fifth
    UnitOfWork repository, promotion recording in
    `ExecutionHandler._promote_goal`, and
    `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence`; edge-case contracts,
    regenerated frontend types, the `happy-path-v2` consumer, and architecture
    documentation are current.
  - **Validated:** 685 non-integration tests passed (1 skipped), 496 integration
    tests passed (6 skipped), ruff and mypy clean, migration head
    `0017_goal_promotions`, frontend type-check/build green, and a live Tier 0
    walkthrough passed all 16 fixture checks using the refs served by the API.

Phase 4 is complete: P4.1–P4.3 close G1, G6, G9, G10 and G11, and the
control-plane contracts are ready for Phase 5's frontend/operator work.

#### Found during P4.3, deliberately deferred

**Accepted evidence is deleted on edit, never retained as superseded** — so the
evidence read model's `superseded_evidence_count` and its
`task_revision == task.revision` filter are unreachable today, and are kept
deliberately as insurance on the endpoint's central claim. Mechanism, the
409 that makes it unreachable, and the warning not to delete either as dead
code are in
[`docs/architecture/known-issues.md`](docs/architecture/known-issues.md).
Whether superseded evidence *should* be retained-and-marked is a frozen-domain
question needing a decision-log entry and an un-freeze, so it was out of scope
for Phase 4.

## Phase 5 — frontend truth and operator UX ✅

**External capability:** a new operator can understand the system, take only
legal actions, recover, and find the verified result.

**Delivered 2026-08-01 on `phase-5-frontend-truth`.** The existing design
system was retained; the work changes authority, coverage, and operator copy
rather than visual language.

- **Status truth:** `Overview` renders canonical status/activity, planning
  operations and retry artifacts, active run, worker lease, TDD stage,
  plan/per-goal blocks, provider backoff, gates, and separate **Needs attention**
  versus **Recovering automatically** queues. This closes G2 and G3 without
  turning capacity recovery into a human block.
- **Control truth:** block controls come from advertised `legal_resolutions`,
  root controls come from `legal_actions`, project-binding recovery is usable,
  and `DetailPanel` can submit a complete `update_task_contract` revision. Chat,
  current goals, navigation, and editing no longer derive cyclic authority from
  the compatibility phase. This closes G4.
- **Execution visibility:** `ConsoleDock` opens bounded captured logs for settled
  attempts and an authenticated fetch-SSE tail for live attempts, including byte
  offset resume, rotation/truncation, reconnect state, and terminal end. This
  closes G5.
- **First-mile setup:** Settings opens on one installation readiness checklist,
  links failed checks to their configuration surface, and includes per-project
  repository readiness. The Plans screen warns when setup is not launch-ready;
  provider/model capacity fields survive create/edit, and plan deletion states
  its cascade and busy-lease refusal. This closes G7 and G8.
- **Last-mile delivery:** preserved cycle history loads accepted command/commit
  evidence, protected scope, rejected/superseded counts, promotion refs and SHA,
  unattributed references, output disposition/reference, and accurate manual PR
  instructions when no forge write was recorded.
- **Legacy isolation:** cyclic canvases, chat, agents, details, status badges, and
  plan lists use `status`, `activity`, `active_cycle`, and advertised actions.
  The nine-phase timeline and review controls remain only for rows carrying an
  explicit `legacy_phase`.

**Validated:** generated OpenAPI types reproduce cleanly; frontend type-check
and production build pass; 8 frontend contract/rendering tests pass; 116 focused
backend unit tests pass; and 127 focused backend integration tests pass with 2
expected skips. Those suites cover capacity preservation, contract repair,
per-goal versus automatic waiting, deletion/binding endpoints, readiness,
planning artifacts, legal actions, evidence, and attempt-log SSE.

### Exit criteria

- ✅ Tier 0 setup, project creation, discovery, gates, and reset have documented
  UI paths backed by the readiness checks.
- ✅ Tier 1 waits, retries, live/captured logs, evidence, promotion, and output
  disposition are visible without terminal-log access.
- ✅ Critical frontend payloads and state separation have stable automated
  contracts in `frontend/src/**/*.test.{ts,tsx}` alongside backend route tests.
- ✅ No cyclic screen presents the nine-phase machine as authoritative.

### Found during the Phase 4/5 code review (2026-08-01)

Five defects were reproduced against the real API, the real capacity resolver,
and the real log tail. None blocked the Phase 5 exit criteria, but all five were
**first-run operator experience**, so they were fixed on `issues-known-defects`
rather than left to preview evidence — each with a test that failed first, and
each recorded in
[`docs/architecture/known-issues.md`](docs/architecture/known-issues.md)
alongside the test that now locks it. **All five are closed:**

1. ~~**Capacity DTOs have no bounds**~~ — **fixed 2026-08-01.** `Field(ge=1)` on
   all three bodies, `capacity_scope` narrowed to a `Literal`, and
   `resolve_max_inflight` skips a non-positive candidate from any door
   (including `execution.provider_max_inflight`, whose stored `"0"` is a truthy
   string). Both failure modes locked on both backends.
2. ~~**`capacity_scope` is an unvalidated free string**~~ — fixed with the above.
3. ~~**scp-style git remotes cannot be bound**~~ — **fixed 2026-08-01.**
   Refused by name, pointing at the ssh:// and https:// forms that work.
   Supporting the form itself would mean changing the clone path and the
   workspace resolver, which never handled it either.
4. ~~**The contract editor over-sends**~~ — **fixed 2026-08-01.** The editor
   diffs against the contract as loaded and submits only changed fields, so a
   command-only repair no longer re-authors the tests or rebinds the agent.
5. ~~**The attempt-log resume offset is per batch, not per line**~~ — **fixed
   2026-08-01.** Each line now carries the byte offset that follows it; the
   client needed no change.

Reviewed and found sound, recorded so the next reviewer need not re-derive it:
the token guard is applied once at mount and parametrized over the OpenAPI
inventory; `block` and `goal_blocks` are both filtered on `.active` before
serving, so no resolved block can reach a client; `requires_human` is projected
onto per-goal blocks as well as the plan-wide scalar; and `goal_promotions`
(0017) declares the plan cascade migration 0015 requires, with
`test_delete_plan_leaves_nothing.py` covering it by name.

### Post-review hardening ✅ (2026-08-01, `issues-known-defects`)

Delivered after the review, before Phase 6 opens:

- **All five review defects fixed**, each with a test that failed first.
- **`reasoner.*` config is no longer boot-time only.** `AppContainer.reasoner`
  returns a `LiveReasoner` that re-resolves per call, so a config write lands on
  the next planning session rather than the next worker restart. Uncaching the
  property alone would not have done it — the worker captures the instance into
  `PlanningHandler` at boot, which is why the fix sits behind the port.
- **`fixtures/first-cycle-v1`** — the onboarding walkthrough (see Phase 6's
  foundation below), proven by two independent Tier 1 runs on free models:
  11/11 expectations and 7/7 live guards each time, from different recovery
  paths (rate-limit retries in one, a rejected candidate re-attempted in the
  other).
- **The deferred backlog moved out of this file** into known-issues, per this
  roadmap's own rule that verified defects are not duplicated here. One entry
  was stale on arrival and is corrected there: goal promotion already retries
  environmental merge failures; only a MOVED cycle branch remains open.

**Validated:** 718 unit + 525 integration tests, ruff and mypy clean, frontend
type-check/build and 15 frontend tests green, migration head `0017`.

## Phase 6 — public-preview productization ✅

**External capability:** an external developer can install the preview, complete
the first walkthrough, understand limits/costs, and share useful evidence.

### Existing foundation

- ✅ `backend/scripts/dev.sh` provides locked setup, doctor, seed, supervised
  startup, and contributor checks.
- ✅ CI builds backend/frontend, checks generated API types, and excludes paid
  LLM smoke tests from normal pushes.
- ✅ Release automation attaches a Python wheel and frontend bundle.
- ✅ The fixture seed is the sample repository; exporters create sanitized
  evidence bundles.
- ✅ `fixtures/first-cycle-v1` is the onboarding walkthrough: **one command**
  (`run-cycle.sh`) drives project → plan → both gates → execution →
  publication → evidence, `preflight.sh` states what must be true before it can
  start (migration head, a live worker, valid bindings, one tier not two), and
  `guards.sh` re-checks the Phase 4/5 critical defects against a LIVE server
  rather than a TestClient. Its judgement layer is locked by
  `backend/tests/integration/test_first_cycle_fixture.py`.

### Deliverables

- Choose and validate one realistic public install path (`uvx`/`pipx`, packaged
  local app, or Docker Compose). One supported path beats several guesses.
- Start API, worker, and packaged frontend with one command or short supervised
  sequence and an explicit state directory.
- Add guided setup/readiness outside a source checkout.
- Publish getting-started, first-plan, Tier 0, pinned Tier 1, troubleshooting,
  and evidence-sharing guides.
- Document security posture, cooperative isolation, unsandboxed runtime limits,
  secrets, supported OS/runtimes, provider costs, known limits, and recovery.
- Reconcile current docs/metadata with project-scoped repositories, goal-level
  execution, cyclic lifecycle, migration head, and live logs.
- Add representative screenshots and a short reproducible demo.
- Validate versioning, changelog, license/contribution/security files, and
  artifact installation on a clean machine.

### Exit criteria — met 2026-08-02

- ✅ **A technical user goes from install to green Tier 0 using public docs
  only.** Verified by doing it: wheel → clean venv → `orchestrate serve` →
  `first-cycle-v1` → 10/10, following the published commands rather than
  repository knowledge. The one thing that would have broken it — `serve`
  leaking its worker on a restart — was found on this path and fixed.
- ✅ **Tier 1 requires explicit model/runtime/cost choices and a readiness
  result.** Tier is data, not an environment variable; `preflight.sh` prints the
  resolved reasoner and runner, fails a mixed pair on purpose, and fails a
  missing CLI only when an agent is bound to it.
- ✅ **Packaged frontend/backend agree on API version/types.** CI regenerates
  the types and fails on drift; the wheel carries the bundle that was built from
  those types, and the browser smoke now proves the packaged bundle boots and
  reaches its own API same-origin.
- ✅ **Docs make no unsupported autonomy, sandbox, SaaS, or forge claims.**
  `SECURITY.md` states the unsandboxed runtime plainly, publication records a
  disposition rather than performing a forge write, and the reconciliation above
  removed the remaining places where the docs described a system that no longer
  exists.

### Delivery status (updated 2026-08-01, `phase-6-public-preview`)

**Install path chosen: a single `uvx`/`pipx` wheel.** Docker was rejected because
the orchestrator runs agent CLIs against the user's own repository with their
git identity and credentials — bind-mounting a repo, translating paths and
reproducing auth inside an image is friction the local-first model does not
need. A packaged desktop app is a larger build surface than any preview evidence
justifies.

**Done, each proved on a clean venv rather than argued:**

- ✅ **Migrations ship inside the package.** `db_upgrade` resolved them from
  `Path(__file__).parents[3]` — the repo root in a checkout, `site-packages`
  when installed, where the only `alembic` is the LIBRARY. An installed copy
  could not create its schema at all. Moved to `agent_orchestrator/infra/db/migrations/`, one
  resolver (`migration_config.py`) derived from its own module location.
- ✅ **The UI ships inside the wheel** and is served by the API
  (`agent_orchestrator/api/frontend.py`), so there is no second artifact and no CORS story on
  the first-run path. The SPA fallback explicitly refuses `api/`, `health`,
  `docs`, `redoc`, `openapi.json` — registering it last is necessary but not
  sufficient, and a test caught it answering unknown API paths with HTML.
- ✅ **`orchestrate serve`** — migrate, API, worker, UI, one command. The worker
  stays a separate process; it terminates gracefully with 30s for the current
  atomic action.
- ✅ **Guided first run** — `/settings/setup` sequences setup in dependency
  order from live catalog state (so it is re-entrant), and Tier 0 never asks for
  an API key.
- ✅ **The release actually ships it.** The workflow built the wheel BEFORE the
  frontend, so every release would have published a backend with no console. Now
  ordered correctly, with a verification step that opens the wheel and refuses
  a release missing the migrations or the UI — demonstrated firing.
- ✅ **Apache-2.0**, PyPI metadata, `SECURITY.md` (written from the code and
  corrected against it), `CONTRIBUTING.md`.
- ✅ **Guides** — `docs/guides/`: getting-started, tier-1, evidence,
  troubleshooting (real failure signatures only).
- ✅ **Test layers the UI never had**: jsdom + Testing Library interaction tests,
  and a light Playwright suite against the packaged bundle. It found a real
  defect on its first run — the packaged UI called a hardcoded
  `http://localhost:8000` regardless of the port `serve` was given, so the
  console loaded and nothing in it worked. Now same-origin.

**Merged 2026-08-02** as [#68](https://github.com/guilhermeUpToTask/agent-orchestrator-prototype/pull/68),
squashed to `main` at `0781d62`; `phase-6-public-preview` is deleted.

Opening the PR was itself a finding: **three CI jobs were red on the branch
before the phase's own changes were reviewed**, all of them fallout from the
packaging move that nothing had exercised. `uv sync --locked` refused a lockfile
still carrying redis and fakeredis, which nothing declares or imports; the codex
plugin's migration-chain check read `backend/alembic/versions`, which stopped
existing when the migrations moved inside the package, and reported the empty
directory as `Heads: []` — the same output a genuinely broken chain produces, so
a moved directory hid behind a chain-integrity error; and the generated API
types drifted because a schema docstring *is* an OpenAPI `description`, so the
`src/…` → `agent_orchestrator/…` rename changed the published contract. All
three fixed on the branch. The browser smoke passed on its first CI run.

- ✅ **The distribution package is `agent_orchestrator`.** Renamed from a
  top-level `src`, which would have collided in site-packages with any other
  distribution doing the same — install order deciding the winner, silently.
  221 Python files plus packaging, tooling and 63 docs/scripts. Three tests
  failed after the bulk rewrite, each locating the source tree by PATH STRING
  rather than by import; verified past the suites by installing the wheel into a
  clean venv and running `orchestrate serve`.
- ✅ **CI runs the frontend tests.** It never had — the Frontend job
  typechecked, built and verified generated types, so every Phase 5 API-contract
  test ran on a laptop and nowhere else.

**Closed 2026-08-02 — the four items the previous session left open:**

1. ✅ **Closing evidence, from the artifact a user installs.** The wheel was
   built, installed into a clean venv outside the checkout, and driven with
   `orchestrate serve` against the real state directory. **Tier 0: 10/10
   expectations, all guards green. Tier 1: 11/11 expectations, 7/7 guards**
   (`plan a603d457`, reasoner `nvidia/nemotron-3-ultra-550b-a55b:free`, `pi`
   runtime on the same free model, $0). The Tier 1 run was clean end to end:
   two runs — test authoring then implementation — one attempt each, no
   capacity events, no retries, `main` byte-identical to the seed tag, the
   accepted evidence naming `python -m pytest -q tests/test_slug.py` exit 0
   against candidate `4620490d`, and `goal/02f5bac7…` promoted into
   `cycle/b3d23fb7…` at `cb7443af`. Intent to publication took about three
   minutes. A **second** Tier 1 run from the same installed wheel
   (`plan 4929491b`, driven to produce the documentation screenshots) also
   passed 11/11 — so the closing evidence is two consecutive green Tier 1 runs
   from the artifact, not one.
2. ✅ **Screenshots and a five-minute demo.** `docs/images/` (committed) holds
   the console during a live run — goal lease, TDD stage, bound agent, accepted
   evidence — regenerated by `frontend/e2e/docs-screenshots.spec.ts` rather than
   cropped by hand. The demo is the fixture, stated first in the getting-started
   guide, and it ends on a checker rather than on trust.
3. ✅ **Architecture-doc reconciliation.** Fixed rather than restated: 60 module
   docstrings still named themselves `src/…` after the rename, `overview.md` drew
   the layers the same way, `PROJECT_REPO_DIR` appeared as the repository source
   in two architecture docs *and as the README's setup instruction* (nothing
   reads it — that is the binding trap documented as advice),
   `execution-model.md` still called execution "sequential per plan" and
   described startup reconciliation as the pre-Phase-2 defect, the state-directory
   layout named a `workspace-repo/` that no longer exists, and the README diagram
   still drew `plan/<id>` branches. What remains is named in known-issues: the
   per-layer READMEs and `INTEGRATION_GUIDE.md` have not been re-read end to end.
4. ✅ **Playwright runs in CI.** The value was demonstrated rather than assumed,
   and the suite now starts its own stack (`orchestrate serve --no-worker` on a
   wiped state directory) instead of requiring a server on port 8210 that
   nothing documented and nothing started. Twelve seconds, one cached Chromium
   download, inside the Frontend job that already installs both toolchains.

**Two defects found in the course of it, both fixed:**

- **`orchestrate serve` leaked its worker on SIGTERM.** uvicorn captures the
  signal, drains, restores the previous handler and re-raises it, so the process
  died inside `uvicorn.run()` and the `finally` that reaps the child never ran.
  `kill <pid>` — systemd, any process manager — stopped the API and left the
  worker claiming plans and spending tokens against the same state directory,
  with no control plane left to report it. Ctrl-C hid it: the terminal signals
  the whole process group. Every existing `serve` test tore down by process
  group, which is exactly why nothing caught it; the regression test signals the
  supervisor alone. Fixed by installing the reaper as a signal handler before
  uvicorn runs, and verified live from the installed wheel.
- **CI was red on this branch before anything was changed.** The package rename
  left `ruff check src tests` and `mypy src` aimed at a directory that no longer
  exists (exit 1 and exit 2), and `--cov=src` made the coverage report silently
  empty rather than failing. The Makefile, CONTRIBUTING, CLAUDE.md and git-flow
  all still told a contributor to run the broken commands.

**Environment notes for whoever picks this up:**

- `~/.orchestrator` holds this phase's completed cycles (five by the end of the
  closing-evidence session) and was migrated `0015 → 0017`. It sits at **Tier 1
  by default**: a Tier 0 run means flipping BOTH `reasoner.mode` and
  `agent_runner.mode`, and flipping them back — nothing restores them for you,
  and `seed demo` would overwrite the free-model bindings outright.
- `frontend/` gained dev dependencies (`@testing-library/react`,
  `@testing-library/user-event`, `jsdom`, `@playwright/test`) and vitest now runs
  in the `jsdom` environment. Playwright's chromium and its OS libraries were
  installed into this container and will not survive a fresh one — run
  `npx playwright install --with-deps chromium` before `npm run test:e2e`.
- `backend/agent_orchestrator/api/static/` is generated by
  `backend/scripts/build_frontend.sh` and git-ignored. A source checkout without
  it starts fine and serves no UI, which is intended.

## Phase 7 — in-product understanding ✅

**External capability:** somebody who has installed the orchestrator can
understand what it is doing, and what they are being asked to decide, without
leaving the console for a repository they may not have read.

**Re-scoped 2026-08-02.** This phase used to be the peer preview itself; the
invitations moved to Phase 9 and the reason is worth keeping, because it
overrules an argument that looks stronger than it is.

The roadmap's own gate says Phase 8 items wait for preview evidence, which
argues for inviting people as early as possible: evidence is cheap, guesses are
expensive, and every week of delay is a week of building on assumption. That
argument is correct about evidence and wrong about invitations, because the two
resources are not alike. **Evidence-gathering is repeatable; a first impression
is not.** There are 10–50 people who will try this because they know the author,
they will each try it roughly once, and a version that installs cleanly and then
shows them a slug-formatting fixture spends that goodwill on the least
interesting thing the system can do. The order that follows is: make it
comprehensible, make it demonstrably good at something, then invite.

The cost is real and should be stated plainly: Phase 8 gets built without the
preview evidence that was meant to prioritize it. The mitigation is that its
scope is now written down with the reasoning (below), so a later report can
still contradict it cheaply.

### Deliverables

- **In-console documentation** at `/docs`, rendering the same `docs/guides/`
  markdown the repository serves. One source, two surfaces: an operator looking
  at a blocked plan should not have to find a GitHub page to learn what the
  block means, and a second hand-maintained copy would drift within a month.
- Conceptual guides the existing set lacks — how the loop works, what each gate
  asks, what every status means and which actions are legal in it, how to read
  the evidence document, where the code goes, and what to do when something
  breaks.
- An `orchestrate version` command. Every run report is supposed to carry the
  orchestrator version (see Run evidence below) and there is no command that
  prints one.
- Keep the feedback template (`docs/guides/preview-report.md`, written
  2026-08-02) current as the surface changes; it is Phase 9's instrument and
  already exists.

### Exit criteria — met 2026-08-02

- ✅ **A user who has never read the repository can answer, from the console
  alone: what is it waiting for, what am I approving, and where did my code go.**
  `statuses.md` maps every status and activity to what to do, `gates.md` covers
  the three decisions, and the delivery block added in #71 answers the third
  from the publication gate itself.
- ✅ **The in-console docs render the repository's own guides, with no second
  copy.** An `import.meta.glob` inlines `docs/guides/*.md` at build time;
  `DocsLayout.test.tsx` fails if the path stops matching, because a broken glob
  renders a full nav over an empty manual and breaks nothing else.
- ✅ **Every guide the console links to describes implemented behavior.**
  `evidence.md` gained the delivery block the endpoint actually serves, and the
  reporting template now calls `orchestrate version` instead of the
  `git rev-parse` workaround it needed while no such command existed.

### Found while finishing (2026-08-02)

- **`/docs` rendered Swagger, not the manual.** FastAPI's default `docs_url` and
  `api/frontend.py`'s reserved-prefix list both claimed the path, so the console
  route was shadowed twice over. Invisible to every test: Swagger and the SPA
  shell are both `200` with HTML, and only reading the content distinguishes
  them. Found by screenshotting the page. Fixed by moving the API explorer under
  `/api/` — everything the API owns now lives there or at `/health` — and locked
  by content-reading tests plus a browser check in the packaged-UI smoke suite,
  which is the only place the two surfaces can compete.
- **The evidence JSON sample was clipped** at the prose measure and scrolled
  sideways with empty space beside it. Prose keeps a reading width; code blocks
  and tables now take the full column.

## Phase 8 — closing the demonstrability gaps 🚧 (current)

**Re-scoped 2026-08-02** from "evidence-driven hardening" to committed work.
The trigger changed: these were deferred pending preview evidence, and the
preview now comes after them, so the ones that gate a credible demonstration
are scoped here and the rest stay deferred.

**Order revised 2026-08-02** after the development environment turned out not to
be able to run containers *with isolation* (see *Containerization was
unavailable* below). `DockerEnvironment` moved from third to **last**, because
it was then the only item in the phase that could not be validated where the
work happens. **That constraint lifted on 2026-08-08** with the `aipom-dev`
guest; the ordering stands because the other items are further along, not
because P8.5 is still blocked:

1. ✅ **P8.1 — the repository-choice wizard** (plus authenticated forge
   publication, promoted into it).
2. ✅ **P8.2 — the `ProjectEnvironment` port and the acceptance-run machinery**,
   with `NoEnvironment` as the only adapter: the seam, its config, its ledger
   and both trigger points, provably inert.
3. ✅ **P8.3 — the per-goal review surface.** Promoted from last to next
   because it is pure addition, blocks nothing, and needs no container runtime.
   `GET …/cycles/{id}/review` splits a cycle into review-sized units and
   `…/review/patch` serves one unit's diff, bounded and reporting truncation.
   The split is the product: a task appears as *the test proven RED first* and
   *the implementation that made it GREEN*, because the orchestrator recorded
   that boundary and nothing else can. Each unit carries its `review_band`
   (from the 87%-under-100-lines research) and the `local_command` that opens
   the same change in the operator's own tools — the browser answers *what
   should I look at first*, the terminal answers *show me*. Read-only, with no
   hunk-level accept/reject: half-accepting a candidate invalidates the
   revision-bound evidence that makes it trustworthy. A garbage-collected SHA
   degrades ONE unit with a stated reason rather than failing the document.
4. 🚧 **P8.4 — the showcase, and it is a DEMO rather than a fixture.** Decided
   2026-08-02. The roadmap already said this artifact "cannot be locked in CI
   the way Tier 0 fixtures are", so filing it under `fixtures/` beside six
   deterministic, checkable, partly CI-locked walkthroughs was a category
   error: someone would try to make it repeatable and conclude something was
   broken. `demos/` states the split, and `demos/README.md` is the contract —
   fixtures catch regressions, demos show what the system produces; a red
   fixture run is a bug, a red demo run is evidence and gets published rather
   than retried.
   - **Shape: `static-site-v1`**, a files-in/files-out generator (markdown +
     front-matter → a browsable HTML site). Chosen against the obvious
     full-stack web app *because* P8.5 is blocked: a web app's goals would end
     as "tests passed, nobody can tell whether it runs", showcasing the exact
     gap this phase exists to close. A generator has no such gap — the demo
     ends with a file you open in a browser, so **a human confirms the product
     works with no container and no trust in the evidence document**, and only
     then reads the evidence for *why*.
   - **Assertions are structural only** (every goal promoted, every served SHA
     resolves, default branch byte-identical to the seed tag, nothing merged
     without accepted evidence, disposition recorded, root back to idle), plus
     an out-of-repo acceptance check on the produced HTML. Nothing asserts a
     goal count: a real reasoner decomposes differently every run, so a pinned
     count would fail a working system.
   - **The run is never in CI; the harness is.** `test_static_site_demo.py`
     locks the three properties that make the demo mean anything — the seed
     does not contain the answer, the brief is postable verbatim, and the
     acceptance check *cannot pass against the seed*. It found a real defect on
     its first run: an unset `SITEGEN_REPO` made `Path("")`, which is `.` and
     always exists, so the skip guard never fired and a forgetful operator got
     eleven errors instead of a clear skip.
   - **Remaining: run it.** Tier 1, real models, captured — and whatever it
     finds gets published. The 2026-08-02 `ORCHESTRATOR_MASTER_KEY` blocker is
     **resolved** (the `aipom-dev` guest has one). **First real attempt made
     2026-08-09; it did not reach publication, and what it found is below.**

   #### First run attempt, 2026-08-09 — reached execution, stopped there

   Project bound to the materialized seed, brief posted verbatim, **intent gate
   and a four-goal cycle draft approved** (front-matter / markdown / layout /
   build — a faithful decomposition of the brief's four requirements). Goal 1
   enriched, its contract frozen, and the **test-authoring stage succeeded**:
   `tests/test_front_matter.py` authored and the `TestBundle` frozen with RED
   evidence. The implementation stage never produced anything. Four findings,
   in descending order of importance:

   1. **The implementer role was bound to a test-author agent — FIXED
      2026-08-09.** The task's `role_agent_ids` resolved BOTH roles to
      `test-agent`, whose instructions begin *"You are a TEST AUTHOR working
      test-first (TDD). Do NOT implement the feature."* So the GREEN stage ran
      an agent explicitly told not to implement. The captured prompt shows the
      contradiction in one screen: `## Your role: implementer` directly above
      the test-author instructions, while four implementer agents in the roster
      were never considered. Not visible at Tier 0, where a single dummy runner
      answers for every role.

      **The cause was structural, not a tie-break accident.** Role resolution
      unioned the ROLE's capability with the TASK's whole
      `required_capabilities` list. A TDD task declares `test_authoring` AND
      `implementation` because it has both stages — a property of the task, not
      a demand on every agent that touches it — so resolving IMPLEMENTER
      required an agent that could also author tests, and the only agents that
      qualify are precisely the ones forbidden to implement. A role now asks
      for its OWN capability plus the task's domain capabilities only, and
      candidates are considered in tiers: agents declaring the role, then
      agents declaring none, then (last resort) agents declaring a different
      one. The last tier has to exist — the default `seed demo` registry is a
      single agent labelled `implementer` holding every capability, and a
      blocked default installation would be a worse bug than the one being
      fixed. Tiers rather than a score are what make it deterministic: a
      dedicated agent wins whatever order the registry was built in.
   2. **An empty completion is misclassified as `rate_limit`.** The runtime
      reported `kind=rate_limit` and the plan settled onto the patient 4×
      rate-limit backoff — but a direct provider call for the same model at the
      same moment returned **HTTP 200**. The pi transcript shows why: the
      assistant turn came back with `content: []` and all-zero token usage.
      Waiting politely for a limit that does not exist is worse than failing,
      because the operator sees "capacity" and assumes patience will fix it.
   3. **Two of four free models are unusable as the reasoner**, in ways worth
      recording because both look like bugs from the outside:
      `nvidia/nemotron-3-ultra-550b-a55b:free` returns turns with `content:
      null` and no `tool_calls` while populating a `reasoning` field, and
      `openai/gpt-oss-20b:free` returns `finish_reason: "error"` mid-generation
      ("provider rejected the request"). **`poolside/laguna-s-2.1:free` planned
      the cycle correctly** and is the one to pin for a Tier 1 rerun.
   4. **A 31-minute gap between an armed retry and the attempt.** Attempt 3
      armed `retry_at` 18:45:47; attempt 4 began 19:16:37, with the worker
      holding a live, renewing plan lease throughout and logging nothing. Not
      yet diagnosed, and not the same thing as the backoff being long — the
      arming timestamp is the thing that was not honoured.

   The run also confirmed machinery working as designed: capacity failures
   discarded their worktrees leaving zero trace, the backoff gate persisted
   across a worker restart, and the failed planning sessions recorded
   `abandoned` artifacts rather than silently vanishing.
5. ✅ **P8.5 — the `ContainerEnvironment` adapter.** Delivered 2026-08-09, after
   the environment blocker was retired by the `aipom-dev` libvirt/KVM guest
   (`infra/dev-vm/`, gate 7/7 — see *Containerization was unavailable* below).
   Selected by `environment.mode=container`, with `environment.container_binary`
   choosing the runtime; validated against real docker AND real podman, not a
   scripted fake. `NoEnvironment` stays the permanent fallback.

### Two blockers parked, both environmental — BOTH resolved by 2026-08-09

Neither was a design problem and neither blocked the other phases. Both needed
the maintainer's own machine, so they are recorded here rather than worked
around. **Both are now resolved by the `aipom-dev` guest**; what remains under
P8.4 is a rerun, not a blocker.

**1. ~~The P8.4 demo run needs `ORCHESTRATOR_MASTER_KEY`~~ — RESOLVED
2026-08-09.** The `aipom-dev` guest has one, readiness is fully green there, and
the run was attempted; see *First run attempt* above for what it found. The
original entry is kept below because its reasoning about the master key is
still the operative warning for anyone rebuilding the guest.

Everything else is
staged and verified against a live server: `orchestrate serve` up on :8000 with
the worker live in `real` mode, `pi` 0.73.1 on PATH, the reasoner and all six
agents resolving to free OpenRouter models, and project `e0e54bc8` bound to
`~/.orchestrator/demos/static-site-v1/repo` (local, git, clean, `main`, seeded
and tagged `static-site-v1-seed`). `GET /api/readiness` returns exactly one
`fail`:

```text
fail  secrets: ORCHESTRATOR_MASTER_KEY is not set, and reasoner and
      agent runner must decrypt a provider key
```

The OpenRouter key is in the database at `secret://provider/openrouter`, and
its data key is wrapped with the master key. **A new master key must NOT be
generated to "fix" this**: it does not reset the store, it makes the existing
secret permanently undecryptable, and the only recovery is re-entering the
OpenRouter API key. Resume by exporting the existing key and posting
`demos/static-site-v1/brief.txt` to project `e0e54bc8`.

Worth noting as evidence rather than annoyance: readiness named the single
cause and the two consumers that need it, instead of a run dying twenty minutes
in on a decrypt error. That is Phase 5's first-mile work doing its job.

**2. ~~P8.5 needs a container-capable host~~ — RESOLVED 2026-08-08.** The
`aipom-dev` guest is that host. See *Containerization was unavailable* below:
the blocker is retired, and the finding it rested on is corrected there.

### Containerization was unavailable in the devcontainer — RESOLVED 2026-08-08 ✅

**Superseded.** The development environment is now the `aipom-dev` libvirt/KVM
guest (`infra/dev-vm/`), which runs nested containers. The capability gate
`make -C infra/dev-vm verify` returns **7 passed, 0 failed** on Ubuntu 24.04.4,
kernel 6.8.0-137, re-run after a kernel upgrade and a full power cycle:

```text
PASS  bwrap mounts a fresh /proc
PASS  fresh procfs in a private PID namespace
PASS  cgroup2 is writable
PASS  cgroup2 mounts in a user namespace
PASS  podman runs with cgroups and a private PID namespace
PASS  docker runs with a private PID namespace
PASS  rootless podman runs with full isolation
```

**The original 2026-08-02 finding was wrong on its central claim**, and the
correction is worth keeping because the wrong version is the more plausible one.
The record said a single kernel rule proved final. It was not one wall — it was
**two walls that deadlocked each other**:

| Blocker | Outcome |
|---|---|
| No `/var/run/docker.sock` | no Docker-outside-of-Docker |
| No `CAP_SYS_ADMIN` (stock Docker capability set) | no `dockerd`, so no classic DinD |
| No `/dev/fuse`, so fuse-overlayfs fails | worked around with the `vfs` storage driver |
| Single-uid userns vs image files owned by gid 65534 | worked around with `ignore_chown_errors` |
| **13 masked `/proc` submounts** (`/proc/kcore`, `/proc/keys`, …) | half of the deadlock |
| **`/sys/fs/cgroup` read-only**, forcing `--cgroups=disabled` | the other half |

Masked `/proc` forbids a fresh `procfs` — an unprivileged user namespace may not
mount one unless it can see a *fully visible* proc instance, and standard
container hardening masks 13 entries with tmpfs — and therefore forbids a
private PID namespace. Meanwhile the read-only cgroup2 tree forced
`--cgroups=disabled`, which **itself disables the private PID namespace**. Each
workaround re-broke what the other needed. Neither alone was terminal; together
they left no path.

The honest statement is therefore **not** "the devcontainer could not run
containers." A hand-rolled OCI bundle *did* run a container there. What the
devcontainer could not do was run containers *with isolation* — no private PID
namespace, by either route. That distinction matters for the adapter, because a
`DockerEnvironment` that merely observes "a container started" would have passed
in an environment that could not actually contain anything.

A third finding, recorded so the instrument is not trusted again: `verify.sh`'s
cgroup-mount check originally used `unshare -Urm`, which leaves the process in
the **initial** cgroup namespace. Mounting cgroup2 from a non-initial userns
needs `CAP_SYS_ADMIN` over the cgroup namespace's owning userns, so it returned
`EPERM` on *any* host, however capable. `-C` fixes it. In the devcontainer that
broken check read as corroborating the read-only-cgroup wall rather than as a
broken instrument — it made a real blocker look worse than it was.

**Two design consequences from the original investigation, both still valid:**

- **The adapter must not hardcode `docker`.** Podman handled everything up to
  the kernel wall and is CLI-compatible; developers running podman, colima or
  rancher would be stranded for no reason. The container binary is
  configuration.
- **"The binary exists but containers do not work here" is a real state**, and
  the retired devcontainer was a specimen of it. The adapter must return
  `errored` with an actionable message rather than hang — which
  `ProjectEnvironment` already contracts for (`verify()` must not raise) and the
  handler already swallows. Keep this even though the guest is capable: the
  state is real on other people's machines, and the devcontainer's own
  half-capability (a container that starts but shares the host PID namespace) is
  exactly the case a naive readiness check waves through.

P8.5 therefore gets **both** halves of its evidence, where it previously could
only get one. The scripted fake container CLI still covers command construction,
output parsing, timeouts, teardown-on-failure, and the not-installed and
daemon-down paths — the pattern the runner taxonomy already uses
(`test_runner_taxonomy.py`). And *does a real container actually boot, isolated*
is now answerable in the environment where the work happens, against real podman
and real docker, rather than deferred to one unrecorded manual run on somebody
else's host.

The showcase project's shape is an open decision and should be made before the
fixture is started, not during it. A full-stack web application is the obvious
choice and the worst one until the acceptance run lands, because the majority of
its goals end as "tests passed, nobody can tell whether it works" — the exact
gap this phase exists to close. A backend-heavy service with real domain rules
plus a thin view, or a files-in/files-out generator, keeps most goals inside
`tdd` where the RED-before-GREEN evidence is the product's actual argument.

**External capability:** the orchestrator can be pointed at a realistic project
and produce a result somebody would want to look at.

The gap that drives this phase: verification modes are `tdd | characterization
| executable_check`, so the system can prove *a command exited 0 against this
commit* but not *the application works*. For a library, a CLI, a parser or a
rules engine that distinction barely matters — the tests are the product's
contract. For the web application most people will imagine when they hear
"builds software", it is the whole question, and the honest answer today is
that nobody can tell from the evidence document.

### Deliverables

- **The cycle acceptance run** (`ProjectEnvironment` port + adapters), specified
  in the deferred list below. This is the one that closes the gap above. The
  port, the ledger and both trigger points shipped in **P8.2**; the
  `ContainerEnvironment` adapter is **P8.5**, unparked 2026-08-08 now that the
  `aipom-dev` guest provides a container-capable environment.
- **A showcase fixture** — one realistic, multi-goal project driven end to end
  on Tier 1, with captured evidence, as the artifact an invitation points at.
  Deliberately NOT a fixture that exercises every capability: several paths
  (contract repair, block resolution, capacity backoff, planning recovery) only
  exist on failure, and `contract-repair-v1` already has to poison a contract to
  reach one. A showcase that breaks on purpose is a bad showcase; capability
  coverage belongs in a separate adversarial fixture.
  It cannot be locked in CI the way Tier 0 fixtures are — a real reasoner
  decomposes goals differently every run — so its assertions are structural:
  every goal promoted, every served SHA resolving in git, the default branch
  untouched, and no goal merged without accepted evidence.
- **The repository-choice wizard** — clone a remote, point at a local
  repository, or create an empty one. Note the two questions it must keep
  separate: *where the code lives* needs no credentials, and *whether we can
  push and open a PR* does. Declining the token must downgrade the delivery
  method, never silently substitute a scratch repository for the project the
  operator named.
- **Authenticated forge publication — promoted out of the deferred list
  2026-08-02**, and delivered with the wizard as **P8.1**. The constraint above
  presumes a delivery method a token *changes*, and none existed: `open_pr`
  recorded that a human opened a pull request, with `output_reference` as free
  text they typed. Shipping the token step before its consumer would collect a
  credential nothing reads, which is the workaround that constraint exists to
  forbid. Scope is bounded hard: a `ForgePort` beside `sandbox_port.py`, a
  GitHub-only adapter, the token per project in the existing secret store, the
  push and the API call **outside** the transaction that records the
  disposition — and the orchestrator opens a pull request but never merges one,
  and pushes `cycle/<id>` but never the default branch. It needs no domain
  un-freeze: the forge binding lives in the project-scoped config store.
  Design: `docs/superpowers/specs/2026-08-02-phase-8-1-repository-choice-and-forge-publication-design.md`.
- **The per-goal review surface**, as specified below.

### Delivery status

- **P8.1 — repository choice and real pull-request publication:** ✅ delivered
  on `phase-8-demonstrability`.
  - Design: `docs/superpowers/specs/2026-08-02-phase-8-1-repository-choice-and-forge-publication-design.md`
  - Plan: `docs/superpowers/plans/2026-08-02-phase-8-1-repository-choice-and-forge-publication.md`
  - **A defect in `main` fell out on the way past:** `_materialize_remote` ran
    `git clone` under `capture_output` with no `GIT_TERMINAL_PROMPT=0`. A
    private `https://` remote makes git prompt for a username with no tty to
    answer it, so the worker blocked indefinitely *while holding a goal lease* —
    no error, no timeout, indistinguishable from slow work. Every git
    subprocess that can reach a remote now runs non-interactively.
  - A project **names** its binding (`local | remote | scratch`) and the API
    refuses a name that disagrees with the URL, which is what stops "remote"
    with a blank URL from silently becoming a scratch repository. Optional, so
    every fixture and `run-cycle.sh` keep working.
  - `POST /api/projects/probe` checks a remote before it is bound and
    classifies the failure (`needs_credentials | not_found | unreachable |
    timeout`); `POST …/clone` materializes it on request. The probe is a
    separate endpoint rather than part of `create`, because
    `repository_binding.py` records a deliberate decision against a network
    check at write time — that reasoning holds for validation and not for setup
    with a human watching, so the two were separated rather than the decision
    reversed.
  - **`open_pr` really opens one** (decision 61): `ForgePort` in `app/`,
    `GitHubForge`, `NoForge` as the permanent fallback, the token per project in
    the secret store and verified at save time. The push and the API call run
    outside any transaction and the disposition is recorded only after the pull
    request exists, so a forge failure leaves the gate open with nothing
    written. No domain un-freeze: the binding lives in the project-scoped config
    store.
  - **Validated:** full backend suite, `ruff` and `mypy` clean, 46 frontend
    tests, production build green.
  - **Not built, deliberately:** the opt-in real-GitHub smoke test needs a live
    repository and a human's token, so it is a manual check rather than a suite
    someone could tick off.

- **P8.2 — the cycle acceptance run (port + machinery):** ✅ delivered on
  `phase-8-2-acceptance-run`. `ProjectEnvironment`
  (`app/environment_port.py`) fills the `cycle_verification` slot
  `planner_orchestrator.py:932` has named since ADR-003 with no behaviour
  behind it. It fires at both designed trigger points — each goal merge (early
  signal) and before the publication gate — records an advisory verdict in
  `acceptance_runs` (migration 0018), and serves it on the cycle evidence
  endpoint.
  - **Advisory is enforced, not asserted.** Tests drive a real cyclic walk and
    prove a `failed` verdict stops neither goal promotion nor the publication
    gate, an adapter that *raises* is swallowed, and a `skipped` verdict records
    nothing — so an empty list reads as "nobody asked" and can never be mistaken
    for a pass.
  - **The pre-publication run happens BEFORE the gate opens**, which is what
    makes the port sufficient. Two defects were found by asking whether the
    domain needed to change, and both had this one cause: `Plan.activity`
    checks `review_gate` **before** it falls through to `cycle_verification`,
    so a run placed after the gate reported `review:cycle_completion` and left
    that label naming an empty slot; and an open gate during a run that takes
    minutes is a race, because a disposition can be recorded against a verdict
    that does not exist yet. Running it in the earlier window makes the
    EXISTING derivation emit `cycle_verification` with nothing added to the
    domain, and the gate opens only once a verdict is recorded. Locked by
    `test_the_gate_is_not_open_while_the_acceptance_run_executes` and
    `test_the_pre_publication_run_fills_the_cycle_verification_slot`.
  - **No domain un-freeze.** The verdict is an operational ledger beside
    `goal_promotions`, not plan state, and the environment spec lives in the
    project-scoped config store. Resolving the repository path goes through an
    injected callable rather than a new method on the FROZEN `Workspace` port.
  - **`NoEnvironment` is the only adapter so far**, and is the permanent
    fallback — most projects the orchestrator gets pointed at are libraries and
    CLIs whose tests genuinely are the contract.
  - **`DockerEnvironment` is next (P8.3)** and deliberately not in this branch:
    no Docker daemon exists in the development container, so the adapter cannot
    be validated here. Shipping an unexercised container adapter behind a green
    suite would be the kind of evidence-free claim this roadmap exists to
    prevent. *(Delivered as P8.5 / `ContainerEnvironment`; see below.)*

- **P8.5 — the container acceptance run, on a VM development environment:**
  ✅ delivered on `phase-8-5-container-environment-adapter`.
  - Design: `docs/superpowers/specs/2026-08-08-phase-8-5-vm-development-environment-design.md`
  - Plan: `docs/superpowers/plans/2026-08-08-phase-8-5-vm-development-environment.md`
  - **The environment came first, and it had to.** `.devcontainer/` was retired
    for the `aipom-dev` libvirt/KVM guest (`infra/dev-vm/`, decision 63,
    capability gate 7/7): the adapter cannot be validated where containers
    cannot run isolated. See *Containerization was unavailable* above for the
    corrected finding — two walls deadlocking each other, not one kernel wall.
  - **Two config keys, deliberately separate.** `environment.mode`
    (`container` selects the adapter; anything else falls back) and
    `environment.container_binary` (default `docker`). Which container CLI
    exists is a property of the MACHINE, so the binary is orchestrator-scoped;
    the boot spec stays project-scoped. An unrecognised mode falls back rather
    than raising — the run is advisory, and a typo must not take down the gate
    it was only observing.
  - **`NoEnvironment` remains the PERMANENT fallback**, like `NoSandbox` and
    `NoForge`. Most projects are libraries and CLIs whose tests genuinely are
    the contract; those record `skipped`, which is not a pass.
  - **Both runtimes are exercised for real.** Every behavioural test is
    parametrized over each container runtime on PATH and passes twice — once
    on `docker`, once on `podman` (16 passed). That double pass is what turns
    "the binary is configuration" from a decision into a tested fact. Tests
    cover a passing scenario, a failing scenario, healthcheck pass and
    timeout, ref isolation, and teardown.
  - **Exactly two cases use a scripted CLI, and only because a live daemon
    cannot be made to take those paths on demand**: the binary is absent, and
    the daemon refuses. Failure injection, stated as such — not a substitute
    for the real-container tests.
  - **The real-container suite earned its cost immediately.** Under parallel
    load it exposed a defect the serial run and any scripted fake would both
    have missed: `run -d` was given `startup_timeout_seconds`, which budgets
    how long the APPLICATION may take to become healthy rather than how long
    the DAEMON may take to accept a detached create. On a loaded machine the
    client call timed out while the daemon created the container anyway — and
    because the teardown `finally` was armed only *after* a successful start,
    that container was never removed. An `errored` verdict AND a leak.
    Teardown now wraps the start and never raises; daemon calls have their own
    budget. Locked by
    `test_a_small_startup_budget_does_not_abort_the_daemon_call`.
  - **The run sees the ref, never the working tree** — a disposable git
    worktree at the commit under test, mounted at `/app`. A verdict attributed
    to a commit that was not what actually ran is worse than no verdict.
  - **No domain un-freeze.** This is an adapter behind `app/environment_port.py`,
    which decision 62 already placed outside the domain.

### Exit criteria

- A realistic multi-goal project completes on Tier 1 with evidence that survives
  independent checking.
- The acceptance run's verdict appears at the publication gate and never blocks
  it.
- Someone who did not run it can look at the captured result and say what was
  built and why they should believe it.

## Phase 9 — small peer preview ⏸

**External capability:** a narrow group can use the orchestrator on disposable
or personal repos and provide comparable evidence.

Was Phase 7 until 2026-08-02; see that phase for why it moved rather than why it
waits. Nothing here is blocked on design — the instrument exists — it is blocked
on having something worth a person's one attempt.

- Invite approximately 10–50 technical users familiar with local CLIs and Git.
- Point the invitation at the Phase 8 showcase result, not at a slug fixture.
- Start with canonical Tier 0/Tier 1 before treating larger projects as evidence.
- Provide an explicit support/issue path. **Not yet built**, and deliberately:
  a support channel with nobody in it is a maintenance cost, and the right
  channel depends on how the invitations go out.
- Measure install/time-to-first-cycle, setup/runtime/repo failures, unclear
  states/actions, recovery, capacity/cost, evidence trust, Git output, and
  missing controls.
- Avoid speculative architecture work while collecting evidence.

### The instrument

`docs/guides/preview-report.md` is the feedback template, written 2026-08-02
before any invitation existed, so its questions were not shaped by what we hoped
to hear. It is a decision instrument rather than a survey: each complaint below
maps to exactly one deferred item, so a report that names it promotes that item
and nothing else.

| What an operator reports | What it promotes |
|---|---|
| "I couldn't find my code" / "I couldn't get it out" | bundle export (the wizard is Phase 8) |
| "the diff was too big to review" | the per-goal review surface |
| "the tests passed but the app was broken" | more acceptance-run coverage |
| "I want it as a PR like everything else" | authenticated forge publication |
| "it got stuck and I couldn't tell why" | the advisory observer agent |

A complaint nobody makes is the cheapest possible answer to a feature question.
This table is weaker than it was when the preview came first — Phase 8 builds
several of these on reasoning rather than reports — so a report that
*contradicts* one is now the most valuable kind, and the template's last
question is written to invite exactly that.

### Exit criteria

- Reports include fixture/version/runtime evidence rather than anecdotes alone.
- Blockers are ranked by frequency, severity, and workflow.
- The next hardening work is selected from repeated user evidence.

## Deferred — reconsider only with run or user evidence ⏸

- stronger sandboxing and pointer-free workspaces;
- ~~authenticated forge publication and automatic GitHub PR creation~~ —
  **promoted to Phase 8 (P8.1) on 2026-08-02**; see that phase. Automatic
  *merging* stays rejected: the orchestrator opens a pull request and a human
  merges it;
- persisted project-wide `ProjectSpec`. **Cycle-wide verification moved to
  Phase 8** as the cycle acceptance run; the design stays here because the rest
  of the entry is still deferred. Designed
  2026-08-02 as a **cycle acceptance run**: a `ProjectEnvironment` port
  (`app/environment_port.py`, beside the existing `Sandbox` port and
  deliberately not a domain concept) with a `DockerEnvironment` adapter and a
  `NoEnvironment` permanent fallback, brings the assembled tree up and runs a
  scenario against it. It fills `Plan._current_activity`'s `cycle_verification`
  label, which today names a slot with no behaviour behind it. Two trigger
  points, one machinery: at each goal merge (early signal) and before the
  publication gate. The operator authors *how to boot it* — image, command,
  port, healthcheck — because LLM-authored boot shell run against a live app is
  the failure mode; the reasoner may propose *what to check*, from the cycle's
  own approved intent. The verdict is **advisory and never blocks
  publication**: a flaky acceptance run that refuses to publish costs more
  trust than it earns, and `start_replan` already exists as the "fix it
  instead" path, so no new `OutputDisposition` value is needed. Two ports, two
  jobs, two words: `Sandbox` is isolation of one task-attempt subprocess
  (bubblewrap, above), `ProjectEnvironment` is containerization of a whole
  project — do not merge them;
- **a per-goal review surface** — **moved to Phase 8**; the design stays here:
  diff and accepted evidence per goal, read-only,
  each view paired with the local command that opens the same thing. A cycle
  branch is one large diff, but the orchestrator recorded the internal
  boundaries — which task produced which commit, which stage was test-authoring
  versus implementation, what the protected scope was — so it is the only
  component that can split a cycle into review-sized units. Review research puts
  defect detection near 87% under 100 changed lines and near 28% over 1,000.
  Explicitly NOT hunk-level accept/reject: half-accepting a candidate
  invalidates the revision-bound evidence that makes it trustworthy, so
  acceptance stays at the granularity the orchestrator can actually verify;
- **an advisory observer agent**: the LLM generalization of the deterministic
  auto-recovery already in `app/` (`agent_feedback.py`, `contract_repair.py`,
  `promotion_failures.py`, `block_policy.py`). It plugs in at the moment before
  a `PlanBlock` opens — diagnose with full run context before escalating to a
  human, and if it cannot fix the cause, attach the diagnosis to the block so
  the operator starts from a hypothesis instead of raw evidence. Event-triggered
  (repeated same-kind failure, block about to open), never streaming: a
  continuous observer on a healthy run burns tokens producing nothing. Advisory
  only — its output is a record, and the human still acts. Giving an agent write
  authority over the aggregate makes a second orchestrator competing with the
  worker for the CAS version, which is rejected;
- **`git bundle` export** (`GET …/cycles/{id}/bundle`): one file carrying the
  cycle's commits and their ancestry, so the receiver sees what base it was
  built against and the evidence document's commit SHAs still resolve. Strictly
  better than a `.zip` of the tree, which discards history, provenance and
  reviewability, and than a `format-patch` series, which is several files with
  no record of the base commit. Likely unnecessary: for a remote-bound project
  `git remote add orchestrator <path> && git fetch orchestrator cycle/<id>` —
  already served by the delivery block on the evidence endpoint — covers the
  same need in one line;
- browser-driven full-cycle Playwright E2E;
- workspace/branch/checkpoint retention and garbage collection;
- richer telemetry analytics, OpenTelemetry, and retention;
- repository indexing, symbol graphs, and context packaging;
- **capacity-budget policy: stop a `request_concurrency` refusal spending the
  per-task retry budget** (deferred out of Phase 2 on 2026-07-28, revisit after
  launch). A concurrency refusal deliberately opens no circuit, so there is no
  `opened_at` for a wall-clock bound to measure and nowhere to record when the
  waiting began; making it budget-neutral without inventing that bound would
  let a permanently saturated provider wait forever with nobody told. Current
  behaviour is wrong but safe and visible. Mechanism and run evidence in
  known-issues; preview evidence should say whether real operators hit it often
  enough to justify a per-task concurrency-wait deadline;
- **an operator command to skip or abandon a wedged task** (Phase 3 audit, G12).
  `Plan.abandon_task` exists and is driven only by exhausted-retry paths; an
  operator facing a task that should not be attempted again has retry, edit, and
  replan — the last being the whole-cycle hammer. Deferred because no
  walkthrough has yet produced a case the other three cannot cover; a preview
  report that names one promotes it;
- proactive concurrent-goal scope-disjointness validation;
- advanced scheduling, load-tested pools, and additional runtimes;
- multi-worker/multi-machine execution, distributed claims, or Redis;
- registry execution profiles/model-tier policy beyond minimum readiness;
- aggregate/handler/router decomposition and schema-diagram tooling unless
  recurring change risk or drift justifies them.

Task-level parallelism, relational goal/task persistence, a continuous domain
reconciler, and a workflow-engine dependency remain rejected absent new
evidence.

Also rejected, 2026-08-02: a **continuously running live preview of in-progress
task work**. Superseded by the cycle acceptance run above, which is cheaper and
produces a verdict rather than an impression. Live preview is a web-frontend
feature in general-purpose clothing — of the shapes an operator will point this
at, single-command frontends and CLIs preview well, HTTP APIs preview weakly,
and libraries, full-stack-with-a-database, and native projects do not preview at
all, where the honest answer is the verification command that already ran and
its recorded exit code. A `.zip` export of the resulting tree is rejected on the
same terms as the bundle entry above.

## Continuous workstreams

### Regression discipline

- Every fixed bug gets a focused regression test.
- Fake and SQLite semantics remain aligned.
- Tier 0 runs for every relevant backend/control-plane change.
- Tier 1 runs after execution, reasoning, verification, runtime resolution,
  capacity, workspace, or publication changes.
- Paid provider tests stay opt-in, never an ordinary per-push cost.
- The suite runs in parallel (`-n auto --dist loadfile`, 2026-08-02): 345s →
  ~135s for all 1258 tests, coverage moved to `make coverage` because nothing
  gates on it. Per-file distribution is load-bearing, not a preference — the
  four `orchestrate serve` tests boot a real API and worker, and the default
  round-robin ran them simultaneously. Parallelism also has to stay honest: a
  test that only passes on an idle machine is a flake with good manners, so a
  change here is validated by a repeated series, not one green run.

### Run evidence

Capture:

- exact brief, fixture version, and orchestrator Git SHA/version;
- reasoner provider/model and agent runtime/provider/model;
- plan/cycle/goal/task/run/attempt IDs;
- timeline, retries, capacity waits, and interventions;
- usage with provenance when available;
- verification evidence and Git refs/disposition;
- defects, fixes, and comparison with previous runs.

Use existing snapshot/bundle exporters as the canonical format.

### Documentation discipline

- Architecture docs describe implemented behavior only.
- `ROADMAP.md` contains future work plus explicit completed foundations, not
  historical implementation plans.
- Verified defects live in
  [`docs/architecture/known-issues.md`](docs/architecture/known-issues.md).
- Historical plans/analyses stay under `docs/history/`.
- Unimplemented features never appear as current architecture.
- Domain changes require a decision-log entry and explicit unfreeze.

### Scope discipline

- Prefer changes that improve installability, first-run success, operator trust,
  recovery, or verified output.
- Do not add endpoints, abstractions, schedulers, or telemetry for completeness.
- Change one fixture variable per run series so evidence stays comparable.

---

Historical roadmaps and analyses remain under [`docs/history/`](docs/history/).
They are evidence, not the current execution order.
