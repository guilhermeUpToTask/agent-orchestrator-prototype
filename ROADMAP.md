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
→ small peer preview
→ evidence-driven hardening
```

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
- Three operator fixtures, all API-only (`curl` + `jq`, no frontend):
  [`happy-path-v1`](fixtures/happy-path-v1/) (the locked one-goal walkthrough,
  Tier 0 and Tier 1), [`planning-recovery-v1`](fixtures/planning-recovery-v1/)
  (a starved planning session leaves evidence the retry can use),
  and [`parallel-goals-v1`](fixtures/parallel-goals-v1/) (two goals promote into
  one cycle branch, so the second merge hits a base the first moved).
  Between them they found the repository-binding trap, an unhandled
  `RoleUnsatisfiableError` that crash-looped the worker, a contract whose
  strategy contradicted its own scope, and capacity failures spending the
  verification retry ceiling.

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
- **A task's checks are identified by declaration** (`src/app/test_identity.py`),
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

## Phase 2 — walkthrough-driven backend hardening ⬜

**External capability:** the tiny workflow survives common failures and either
recovers automatically or tells the operator exactly what to do.

The recovery architecture exists. Validate it with Tier 0/Tier 1 evidence
instead of redesigning it speculatively.

Recovery work completed against run evidence this cycle is listed under the
implemented foundation above; what remains here is the evidence still to
collect, not a redesign.

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

Fixed by a per-scope curve in `src/app/provider_capacity.py`
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

Not exercised: `contract_repair` never fired, because the agent succeeded this
time. The deadlock fix therefore remains verified by its regression test and by
the absence of the failure mode, not by watching a repair persist live. That is
still the open item for the next red run.

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

### Deferred — goal-promotion auto-rebase, pending run evidence

`goal_promotion_failure` is the only block kind with **no observed run
evidence**. It opens on the FIRST exception from `merge_goal`, with no retry at
all — including for a transient git or filesystem error — and advertises a single
resolution, `start_replan`.

The obvious repair is to rebase the goal branch onto the moved cycle branch and
retry the merge. It is deliberately NOT built yet, for two reasons:

- **The failure has not been reproduced.** `fixtures/parallel-goals-v1` exists to
  provoke it: two independent goals, one cycle branch, so the second merge runs
  against a base the first already moved. It passes — both goals DONE, both
  merged, no block. Under dry-run each task writes its own artifact, so two goals
  never touch the same file and cannot conflict; a genuine conflict needs Tier 1
  with two goals over overlapping scope, which is the next evidence to collect.
- **A rebase is not "retry the merge".** Goal→cycle promotion currently runs no
  verification of its own: `_reserve_goal_promotion` checks each task is DONE
  with accepted evidence, and that evidence was produced per task, on the task
  branch, against an OLDER base. Rebasing recombines verified code with code it
  was never tested against, so the repair must also introduce a goal-level
  re-verification step that has never existed — the orchestrator re-running the
  tasks' `verification_commands` on the rebased tree. Skipping that would move
  unverified work upward and break the invariant the whole system rests on.

The smaller, safer fix available today, if evidence never materialises: retry a
merge that failed for a transient reason before blocking, and stop advertising
`start_replan` as the only way out of a git error that a retry would clear.

### Deferred — no operator control point at the contract boundary

A worker enriches a goal and executes its first task under ONE claim, and the
pause gate blocks *claims* — so arming a pause before enrichment stops enrichment
too, and arming it after is unwinnable: there is no window. A plan sitting at a
review gate is `waiting` and cannot be paused at all. Net effect: nothing outside
the process can hold a plan between "the contract is frozen" and "an agent is
running against it".

Found while trying to build a live fixture for contract repair (un-freeze #17),
which needs exactly that boundary. The behaviour is arguably correct — the JIT
loop exists to avoid planning work nobody asked for — but it means a whole class
of contract-level operator scenario can only be tested by stopping the worker,
which is outside an API-only walkthrough. Those scenarios are covered at the
orchestration level instead (`tests/unit/orchestration/test_contract_editing.py`
drives both handlers a step at a time, on both backends).

If it is ever picked up, the smallest useful lever is a review-gate-like hold at
the contract boundary — opt-in per plan, so the JIT loop stays the default — not
a general "pause between units", which would turn every unit boundary into a
scheduling decision.

### Deferred — reasoner config is boot-time only

`AppContainer.reasoner` is a `@cached_property` and the worker resolves it once
at startup, so every `reasoner.*` config key — `mode`, `provider_id`, `model_id`,
`temperature`, `max_turns` — takes effect only after a worker restart. Writing
one through `PUT /api/config/orchestrator/...` succeeds, `GET /api/reasoner/status`
reports the new value (the API process builds its own), and the worker keeps
using what it booted with. Found while building `planning-recovery-v1`, whose
whole mechanism is changing `reasoner.max_turns` mid-run: the change was accepted
and silently ignored.

Caching is deliberate — rebuilding per tick would re-read the secret store and
re-resolve the catalog on every poll — so this is a **staleness** decision, not a
bug to patch blindly. Whoever picks it up chooses between: a cheap generation
counter on the config table that the worker compares each tick and invalidates
on change; scoping invalidation to the keys that are safe to swap mid-flight (a
model swap between attempts is fine, a mode swap mid-session is not); or leaving
it boot-time and making that explicit in `GET /api/reasoner/status` plus the
config API response, so an operator is told a restart is required instead of
watching a successful write do nothing. The last option is the smallest honest
fix and probably the right first move.

Same question applies to `agent_runner.*`, which resolves per task per run and
is therefore probably already live — verify rather than assume.

### Deferred — planning-artifact retention

`planning_artifacts` (migration 0014) keeps what a failed planning attempt
produced so the retry starts better informed. Nothing prunes it, and **that is a
deliberate deferral, not an oversight**:

- Reads are already bounded — keyed by `(plan_id, purpose, goal_id)` with
  `limit=5` and served by `ix_planning_artifacts_lookup` — so growth costs
  storage, never read latency or correctness.
- A replan mints new goal ids (`activate_cycle`), so a superseded cycle's rows
  become permanently unreachable rather than wrong. A long-lived plan through
  several replans therefore accumulates rows nothing will ever read again.
- `ON DELETE CASCADE` from `plans` already collects them when a plan is deleted.

What a retention sweep would need to decide, whenever it is picked up: keep the
last N attempts per `(plan, purpose, goal)` versus an age bound; whether
`committed` outcomes are worth keeping at all once the contract they produced is
frozen; and whether pruning belongs in the store's `append` (cheap, amortised) or
in a maintenance command (visible, operator-triggered). Do not add a background
sweeper for it — this codebase has no scheduler, and inventing one for garbage
collection would be exactly the coordination infrastructure this phase says not
to introduce without run evidence.

### Exit criteria

- No launch-critical defect can hot-loop, corrupt state, promote unverified
  work, touch the default branch, or advertise an unusable recovery.
- Repeated unexpected worker exceptions cannot starve healthy plans.
- Operators can distinguish active work, capacity/backoff waiting, graceful
  pause, recoverable block, and external terminal failure from persisted facts.
- Relevant regressions pass the baseline fixture contract.

## Phase 3 — capability-to-product coverage audit ⬜

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

### Exit criteria

- No endpoint is proposed merely for completeness; every launch-critical row
  maps to a walkthrough operator job.
- Nine-phase fields/routes are labelled compatibility-only.
- Each launch-critical gap has an owner phase (4, 5, or 6) and objective test.

## Phase 4 — API control-plane completion ⬜

**External capability:** a technical operator can set up, drive, inspect,
recover, and publish the happy path without database surgery.

Close only launch-critical matrix gaps. Investigate:

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

## Phase 5 — frontend truth and operator UX ⬜

**External capability:** a new operator can understand the system, take only
legal actions, recover, and find the verified result.

The React shell, composer, settings CRUD, gates, status surface, attempt history,
and SSE bridge are foundations. Improve them in this order:

1. **Status truth**
   - Render root status, activity, planning operation, current work/TDD stage,
     gates, plan/per-goal blocks, active run, provider waiting, retry/backoff,
     and legal actions from backend truth.
   - Do not turn capacity waiting into a human block or hide independent blocks.
   - Distinguish **"waiting, recovering automatically"** from **"needs you"**.
     `src/app/block_policy.py` already records `requires_human` per block kind
     and is the single source for what a block may advertise; the API does not
     serve it yet, so the frontend cannot tell an operator whether a block is
     their problem or the orchestrator's. Serve it alongside `legal_actions`,
     and surface the automatic loops (contract repair, promotion retry, planning
     replay) as progress rather than a silent spinner — they are recorded at
     `GET /api/plans/{id}/planning-artifacts`.

2. **Control truth**
   - Ensure every visible action is legal and functional.
   - Stop using legacy `phase` to control cyclic chat, editing, navigation, or
     recovery; isolate compatibility rendering to legacy plans.

3. **Execution visibility**
   - Consume per-attempt log SSE, including rotation, offset resume, and end.
   - Show running command/stage, attempt, retry reason, wait, verification
     progress, and bounded persisted history.

4. **First-mile setup**
   - Guide project/repository binding, provider/model creation, reasoner
     selection, agent/runtime binding, coverage, dependency probes, and secrets.
   - Present one readiness checklist and clear first-plan launch path.

5. **Last-mile delivery**
   - Show accepted evidence, promoted branch/output reference, and disposition
     consequences.
   - Give accurate manual PR instructions; add a thin helper only if evidenced.

6. **Legacy cleanup**
   - Remove/isolate nine-phase timelines, labels, toasts, and phase-derived
     controls for cyclic plans.
   - Use backend `legal_actions`, not reconstructed React transition rules.

Use Playwright selectively for stable setup, gate, recovery, and publication
contracts. Do not block on a visual redesign or full real-agent browser E2E.

### Exit criteria

- A first-time operator completes Tier 0 in the UI without undocumented setup.
- Tier 1 waits, retries, evidence, and output are clear without terminal logs.
- Critical actions have stable automated contracts.
- No cyclic screen presents the nine-phase machine as authoritative.

## Phase 6 — public-preview productization ⬜

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

### Exit criteria

- A technical user goes from install to green Tier 0 using public docs only.
- Tier 1 requires explicit model/runtime/cost choices and a readiness result.
- Packaged frontend/backend agree on API version/types.
- Docs make no unsupported autonomy, sandbox, SaaS, or forge claims.

## Phase 7 — small peer preview ⬜

**External capability:** a narrow group can use the orchestrator on disposable
or personal repos and provide comparable evidence.

- Invite approximately 10–50 technical users familiar with local CLIs and Git.
- Start with canonical Tier 0/Tier 1 before treating larger projects as evidence.
- Provide one feedback template and explicit support/issue path.
- Measure install/time-to-first-cycle, setup/runtime/repo failures, unclear
  states/actions, recovery, capacity/cost, evidence trust, Git output, and
  missing controls.
- Avoid speculative architecture work while collecting evidence.

### Exit criteria

- Reports include fixture/version/runtime evidence rather than anecdotes alone.
- Blockers are ranked by frequency, severity, and workflow.
- The next hardening work is selected from repeated user evidence.

## Phase 8 — evidence-driven hardening and expansion ⏸

Take these up only when preview evidence proves the need:

- stronger sandboxing and pointer-free workspaces;
- authenticated forge publication and automatic GitHub PR creation;
- persisted project-wide `ProjectSpec` and cycle-wide verification;
- browser-driven full-cycle Playwright E2E;
- workspace/branch/checkpoint retention and garbage collection;
- richer telemetry analytics, OpenTelemetry, and retention;
- repository indexing, symbol graphs, and context packaging;
- proactive concurrent-goal scope-disjointness validation;
- advanced scheduling, load-tested pools, and additional runtimes;
- multi-worker/multi-machine execution, distributed claims, or Redis;
- registry execution profiles/model-tier policy beyond minimum readiness;
- aggregate/handler/router decomposition and schema-diagram tooling unless
  recurring change risk or drift justifies them.

Task-level parallelism, relational goal/task persistence, a continuous domain
reconciler, and a workflow-engine dependency remain rejected absent new
evidence.

## Continuous workstreams

### Regression discipline

- Every fixed bug gets a focused regression test.
- Fake and SQLite semantics remain aligned.
- Tier 0 runs for every relevant backend/control-plane change.
- Tier 1 runs after execution, reasoning, verification, runtime resolution,
  capacity, workspace, or publication changes.
- Paid provider tests stay opt-in, never an ordinary per-push cost.

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
