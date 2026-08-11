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
- ⬜ Planned — required before launch
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
→ frontend refinement, refactor and browser-proven behaviour
→ repository audit, then launch
```

The last three were reordered on 2026-08-02: the preview used to come before
the gap-closing work. See Phase 7 for the reasoning and for what that reorder
costs.

**Re-scoped again 2026-08-10, after Phase 8 closed.** Phase 9 was a *small peer
preview*, and it is **deleted, not deferred**: it assumed an invitation list
that does not exist, and a phase whose entry condition is "find 10–50 strangers"
is not a plan, it is a wish. Its feedback template went with it — a survey for
an audience that does not exist is documentation nobody reads and everybody has
to keep current. Gathering feedback is a real activity, and it belongs to
whatever Phase 10B's launch actually produces, designed then against a real
audience rather than an imagined one.

What replaces it is the work that has to happen **before** anyone is invited
anyway: the console is the first thing a visitor touches, and it is currently
the least examined surface in the repository.

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

## Phases 0–7 — delivered ✅

The full delivery record — measurements, defect post-mortems, exit-criteria
evidence and dated status for every closed phase — is archived verbatim in
[`docs/history/2026-08-10-delivered-phases-0-7.md`](docs/history/2026-08-10-delivered-phases-0-7.md).
It moved there on 2026-08-10 because this file's job is what is NOT yet built.

| Phase | External capability it delivered | Closed |
|---|---|---|
| 0 — reproducible validation baseline | One free, deterministic walkthrough proving lifecycle, gates, worker, Git/verification, publication, API and UI wiring against the same disposable repository every time | 2026-07-27 |
| 1 — Tier 1 real-runtime happy path | The same walkthrough against a real provider and a real CLI runtime, with the evidence to show it ran | 2026-07-27 |
| 2 — walkthrough-driven backend hardening | The defects only a real operator session finds: capacity backoff ignoring `limit_scope`, a crashing plan starving healthy ones, the Tier 1 series | 2026-07-28 |
| 3 — capability-to-product coverage audit | [`docs/architecture/capability-matrix.md`](docs/architecture/capability-matrix.md) — what is supported and where it is exposed, with the route inventory test-locked | 2026-07-28 |
| 4 — API control-plane completion | Every operator workflow reachable over the API, not just the ones the UI happened to surface | 2026-08-01 |
| 5 — frontend truth and operator UX | The console rendering backend `status`/`activity`/`legal_actions` instead of rebuilding transition rules, and the first-mile readiness work | 2026-08-01 |
| 6 — public-preview productization | One installable artifact: `orchestrate serve`, the packaged UI in the wheel, onboarding and packaging | 2026-08-02 |
| 7 — in-product understanding | The in-console manual rendering the repository's own `docs/guides/*.md`, so there is exactly one copy of every guide | 2026-08-02 |

## Phase 8 — closing the demonstrability gaps ✅ (complete 2026-08-10)

**All seven items delivered.** The phase ended the way it was supposed to: with
a demo that completes and is published rather than asserted — 5 of 5 goals in
**13m 03s** for **$0.0134**, 10 attempts and zero failures, against a baseline
that reached 61 minutes with zero goals promoted. Runs in
[`demos/static-site-v1/runs/`](demos/static-site-v1/runs/); measurement and
attribution in
[`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md).
Kept below rather than archived because it is recent and its reasoning is still
load-bearing; it moves to `docs/history/` when Phase 9 closes.

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
because P8.5 is still blocked.

**Extended 2026-08-09 with P8.6 — refining.** The first real Tier 1 runs showed
the phase can deliver every capability it promised and still fail its own
purpose: a five-goal cycle sat at 61 minutes with nothing promoted. A showcase
nobody will sit through does not demonstrate anything, so closing the latency
gap is scoped into the phase rather than deferred past it.

**Extended again 2026-08-10 with P8.7 — refactoring, which ran BEFORE P8.6.
Both are now delivered, and P8.4's rerun with them: the phase is complete.**
The execution order was **P8.7 → P8.6 → P8.4's rerun**. The reason P8.7 went first was
not tidiness: P8.6's two largest targets both land inside what was a 2,133-line
class with 44 methods, and the three defects that cost 2026-08-09 escaped 1400
green tests because they were *inexpressible* in the fakes rather than missed.
Optimising a system whose tests cannot fail on its real failure modes measures
nothing, and changing behaviour inside code that cannot be read is how those
defects arrived. That class is now 1,435 lines across 25 methods, its longest
function 195 lines, and the fakes can express what they used to discard.

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
4. ✅ **P8.4 — the showcase, and it is a DEMO rather than a fixture.
   COMPLETED 2026-08-10** with two clean runs, captured in
   [`demos/static-site-v1/runs/`](demos/static-site-v1/runs/) — quote the
   second, `20260810T164908Z-d098aece` — and measured in
   [`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md).
   Decided
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
6. ✅ **P8.6 — refining: make a cycle finish in a time somebody will wait for.
   DELIVERED 2026-08-10, and with it P8.4's rerun.** Scoped 2026-08-09 from measured evidence rather
   than from intuition — see
   [`docs/history/analyses/2026-08-09-cycle-latency-analysis.md`](docs/history/analyses/2026-08-09-cycle-latency-analysis.md).
   A five-goal cycle sat at **61 minutes with zero goals promoted**, never
   blocked, and **62% of execution wall-clock produced nothing** (38%
   productive, 20% burned on failed attempts, 42% idle in backoff).

   **The measured runs:
   [`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md).**
   Two cycles, both **completed** — every goal promoted, publication gate
   reached, disposition recorded, root back to IDLE. The one to quote is the
   second, because it is **5 goals like the baseline and uninterrupted**:
   **13m 03s** for **$0.0134**, **10 attempts and zero failures**, **92.5%
   productive, 0% wasted, 7.5% idle** — and that 7.5% is entirely JIT
   enrichment for the next goal, so genuine idle is zero. Against 61 minutes
   with zero goals promoted, 38% productive and 42% idle.

   **Read the attribution before the numbers.** The dominant term is the one
   the plan predicted and it is not this phase's code: the free tier was
   removed on both sides at once (paid reasoner, `codex` agents on a
   subscription). What is genuinely attributable is narrower — parallel
   enrichment fired in both runs and is visible in the ledger (the two
   independent goals, one pass, 8s and 4s apart), unbuffered logs carried every
   diagnosis in the session, and **capacity routing was never exercised at
   all** — nothing was rate-limited in 18 attempts, and a codex-only roster has
   no sibling provider to route to anyway. It is locked by tests, not by these
   runs.

   This is a demonstrability problem, not just an ergonomics one: P8.4's
   showcase is the artifact an invitation points at, and nobody watches an hour
   of nothing. The ordered targets:

   1. **Enrich ready goals in parallel.** The one structural change with the
      largest payoff. Enrichment is JIT *and* strictly serial — goal 2's
      session starts the second goal 1's commits — so five goals cost ~25
      minutes of pure sequencing. `ready_goal_ids` already computes the
      parallelism-safe set and the execution loop already honours it under
      `max_concurrent_goals`; enrichment simply does not use it.
   2. ~~Stop misclassifying an empty completion as `rate_limit`.~~
      **RETRACTED 2026-08-09 — there is no such defect.** Every `rate_limit`
      attempt in both runs carries a genuine upstream code (`429`
      "temporarily rate-limited upstream", `RESOURCE_EXHAUSTED` "Worker local
      total request limit reached"); none lacks one. The original claim
      compared an HTTP 200 probe of `nemotron` against a failure produced by
      `gemma` — a different model — and read the resulting `content: []` as a
      misdiagnosis rather than as the consequence of a refusal. **Do not
      implement this**: making the runtime impatient with real 429s is the
      specific harm the patient curve exists to avoid. See `known-issues.md`.
      The 42% idle share is therefore real capacity exhaustion on a free tier,
      and the answer to it is item 3 (route to a model that is not exhausted)
      plus paying for a tier that is not.
   3. **Rotate models on a capacity failure rather than waiting on one.**
      Tier-ordered routing to another capability-satisfying agent exists
      (un-freeze #16) but engages through the admission gate and circuits; a
      task that merely got rate-limited waits on the same binding while three
      other implementers sit idle. Must not mutate the persisted binding.
   4. **Cap generation size and let stage pick the model tier.** One reasoner
      response carried **22,242 completion tokens** — a latency cost before it
      is a token cost. `AgentSpec.model_role` (`smart`/`cheap`) exists for the
      second half and bought nothing here, because everything was pinned to
      similar-latency free models.
   5. **Diagnose the 31-minute gap between an armed `retry_at` and the
      attempt**, with a live renewing lease and no log in between.
      **NOT REPRODUCED 2026-08-10, and deliberately not closed.** The complete
      set of gaps in the second run was `0.0s 18.7s 102.7s 0.6s 0.3s 24.7s
      0.0s` — the 102.7s an operator pause, the other two the JIT enrichment
      sessions for the next goal. But no attempt in that run ever entered
      backoff, so the code path the ghost lives on was never taken: this is
      "not reachable by that run's shape", not "fixed". Look again the next
      time a run actually backs off.
   6. **Unbuffer the supervised worker's logs.** `serve` runs the worker as a
      subprocess whose stdout is not a tty, so the log sits frozen at the
      startup banner while attempts run. This cost real diagnosis time in the
      P8.4 run and needs no design work.

   **Measure before and after on the same fixture.** The honest baseline is the
   analysis document above; anything claimed here without a second measured run
   is the kind of evidence-free assertion this roadmap exists to prevent. Note
   too that items 2 and 3 are free-tier-shaped, while 1, 4 and 6 are structural
   and pay off on any provider.

   **Spend a little money before optimising anything.** The free tier is the
   largest confound and the cheapest to remove: measured consumption puts a full
   five-goal cycle at roughly **$0.06–$0.12**, so a dollar buys 8–17 complete
   runs. Budget is not the constraint — reliability is, because a failed run
   costs an hour of wall-clock and a rerun. Pick the most reliable tool-caller
   in the cheap tier, not the cheapest model. **Confirmed and then some,
   2026-08-10: the completed run cost $0.013 — 77 runs per dollar — on
   `deepseek/deepseek-v4-flash-0731`.** The plan's price table is stale for
   `z-ai/glm-5.2`, which it recommends as the reasoner at $0.07/$0.22 per Mtok;
   the live price is $0.76/$2.42, 10x, which would make it ~$0.25 a run.

   Plan: [`docs/superpowers/plans/2026-08-09-phase-8-6-refinement-and-demo-completion.md`](docs/superpowers/plans/2026-08-09-phase-8-6-refinement-and-demo-completion.md),
   which also carries the model comparison and the demo-completion checklist.

   **No conclusion about the orchestrator's capability follows from the
   2026-08-09 runs.** Three defects sat in the path — the implementer role bound
   to an agent instructed not to implement, planning unable to submit at all,
   and empty completions misclassified as capacity limits — and the first two
   were only fixed at the end of that session. ~~The demo has not yet had one
   clean attempt.~~ **It has now — two, 2026-08-10:
   [`…runs/20260810T133717Z-aaedbb73/`](demos/static-site-v1/runs/20260810T133717Z-aaedbb73/)
   and the clean rerun
   [`…runs/20260810T164908Z-d098aece/`](demos/static-site-v1/runs/20260810T164908Z-d098aece/).**
   Four defects surfaced during the first and were fixed in flight with
   regression tests — none in the orchestration core, and the one worth
   remembering is that the demo's own content contradicted its own brief,
   producing a visibly duplicated `<h1>` that the container acceptance run, the
   7/7 structural checks and all eleven out-of-repo acceptance assertions
   **all passed**. The eye check found it, exactly as `demos/README.md`
   promises it would. **The rerun surfaced nothing**, and the finding is now
   asserted in three places — the acceptance suite, a container scenario step,
   and a seed contract test — so the next run does not need eyes.

7. ✅ **P8.7 — backend and documentation refactoring. DELIVERED 2026-08-10.**
   All six tasks complete. Scoped from measurement:
   [`docs/history/planning/2026-08-10-backend-refactoring.md`](docs/history/planning/2026-08-10-backend-refactoring.md).

   **Why it comes first, ahead of the latency work and the demo rerun.** P8.6's
   targets (parallel enrichment, capacity routing) both land inside
   `ExecutionHandler` — a **2,133-line class with 44 methods** holding the two
   longest functions in the tree (303 and 288 lines). Changing behaviour there
   before it can be read is how the last three defects happened. And every one
   of those defects escaped 1400 green tests not through carelessness but
   because it was **inexpressible**: each fake was handed the input that
   mattered and discarded it. Optimising a system whose tests cannot fail on its
   real failure modes measures nothing.

   The ordered tasks (full detail in the plan):

   1. ✅ **Adversarial fakes.** Delivered 2026-08-10. A fake that discards an
      input cannot fail on it: the audit found `NoOpWorkspace.begin` dropping
      `base_ref`/`cycle_id`/`goal_id`/`run_id`, `merge_goal` recording nothing,
      and `DummyAgentRunner.run` dropping the workspace — so a task branch cut
      from the wrong base, a goal promoted into the wrong cycle branch, and an
      agent working outside its worktree were all untestable.
      `GreedyReaderLLMClient` reacts to the tools it is OFFERED rather than
      replaying a script, so it can *choose* to keep reading; reverting
      `reserved_submit_turns` makes both new tests fail with the exact
      production error. No production code changed.
   2. ✅ **A runtime registry.** Delivered 2026-08-10.
      `infra/runtime/registry.py` is now the single description of a runtime;
      `RUNTIME_TYPES`, the dependency-probe table and the factory's dispatch all
      derive from it, and `test_runtime_registry.py` fails if they can disagree.
      A sixth runtime is **one registration** instead of four coordinated edits
      that failed silently when one was forgotten. The factory's four-branch
      if-chain became a lookup (385 → 355 lines) and its repeated nine-argument
      construction became one `RuntimeBuild` parameter object. codex's
      `needs_api_key=False` is a DECLARED field, not a hidden default — the
      factory reads it to decide whether to touch the secret store at all.
   3. 🚧 **Split `ExecutionHandler`** in risk order. **IN PROGRESS — this is
      where a new session resumes.**
      - ✅ **Step 1 (2026-08-10): pure helpers extracted** to
        `app/handlers/execution_rules.py` — `Unit`, `orchestration_failure`,
        `main_repo_failure`, `raise_on_infrastructure_exit`,
        `author_path_allowed`, `unit_task`, `jitter_unit`,
        `attempts_against_budget`. Chosen first because every one was already
        `@staticmethod` or took `self` without using it, so the move cannot
        change behaviour. **2328 → 2220 lines; the class 2133 → 2050, 44 → 38
        methods.** Test count unchanged at 1413, which is the proof.
      - ✅ **Step 2 (2026-08-10): agent selection + admission** moved to
        `app/handlers/agent_admission.py` as `AgentAdmission` —
        `resolve_spec`, `select_spec`, `provider_metadata`, `_spec_wait_seconds`,
        `admission_signal`, `circuit_signal`, `clear_circuit`. Selection and
        admission ship together because selection asks "is this provider free?"
        using the same circuit and in-flight facts admission enforces; splitting
        them would duplicate the reads and let the answers drift. The
        collaborator now owns the agent catalog, the provider catalog and the
        routing policy — **`ExecutionHandler` reads none of the three directly**.
        It is constructor-injected (optional trailing parameter, built from the
        existing collaborators when absent), so every caller including the tests
        that construct the handler positionally is unchanged. `run_role_for`
        moved to `execution_rules.py`: its two callers now live in different
        modules and it must stay one definition. **2220 → 1999 lines; the class
        2050 → 1846, 38 → 31 methods.** Test count unchanged (1436 collected,
        1429 passed / 7 skipped before and after), which is the proof.
      - ✅ **Step 3 (2026-08-10): goal promotion + acceptance** moved to
        `app/handlers/goal_promotion.py` as `GoalPromoter` — `reserve`,
        `promote`, `block_unpromotable`, `pending_acceptance_cycle`,
        `run_acceptance`, and the bounded `_retry` for an environmental merge
        failure. One concern with a strict order (reserve, merge, re-guard,
        record, observe); the acceptance run travels with it because the two
        points where a cycle branch changes meaning are a goal merge and the
        moment before the publication gate opens. **1999 → 1673 lines; the
        class 1846 → 1530, 31 → 25 methods.**
      - ✅ **Step 4 (2026-08-10): the finalize monster.** The plan's "303 and
        288 lines" were ONE function measured twice — `_finalize_failure` and
        its nested `finalize()` closure. Now **112 lines**. Three seams, each
        placing the moved code where its data already lives:
        `AgentAdmission.record_capacity_failure` (the ~110-line circuit block,
        which is the WRITE side of rows the same class already read, returning
        a `CapacityOutcome` instead of three loose locals),
        `execution_rules.retry_delay_seconds` (the backoff math, pure), and
        `_abandon_late_failure` / `_open_execution_block` (still handler
        methods — both mutate the plan). `_finalize_success` (80 lines) needed
        nothing. **1673 → 1574 lines; the class 1530 → 1435.** The longest
        function in the tree is now `handle` at 195.

      Public surface (`handle`, `handle_goal`, `finalize`) did not change at any
      step, and no step changed a test assertion. Three tests in
      `test_contract_editing.py` called `handler._promote_goal` by name and now
      name its new home.
   4. ✅ **Parameter objects** for the worker drive path. Delivered 2026-08-10.
      `drive_goal` took **20 parameters, 11 optional collaborators**;
      `app/execution_services.py` bundles them and
      `AppContainer.execution_services` is the ONE place the set is enumerated.
      The measured cost of the old shape was two live omissions, both of exactly
      one argument in exactly one place: the worker never passed
      `environment`/`environment_context`, so **the P8.2/P8.5 acceptance run was
      dead code in production** (fixed in its own commit, locked by
      `test_worker_pool.py` driving the real entrypoint), and `PlanDispatcher`
      never passed `routing`. `drive_goal` is now 8 parameters, `worker_tick` 5,
      `advance_plan` 4. `execution_services` is deliberately NOT cached — the
      capacity and routing policies are re-read per access so `config set`
      applies without a restart.
   5. ✅ **Router split and the `pause_resume` rename.** Delivered 2026-08-10.
      `plans.py` (1626 lines) became a PACKAGE — `schemas` plus `lifecycle`,
      `read`, `cycles`, `control`, `conversation`, `telemetry`, each 95–339
      lines, composed into one router. Route paths are byte-identical, which
      `test_capability_matrix.py` proves by comparing the served OpenAPI
      inventory against the matrix. `use_cases/pause_resume.py` became
      `operator_commands.py`: it held five use cases and only two were in its
      name.
   6. ✅ **Documentation.** Delivered 2026-08-10. 6,673 lines of executed plans
      (P4.1–4.3, P8.1, P8.5) moved to `docs/history/planning/` with rows added
      to the archive index, leaving only in-flight plans live. ROADMAP **2026 →
      947 lines**: the 1,097-line delivery narrative for Phases 0–7 is archived
      verbatim at `docs/history/2026-08-10-delivered-phases-0-7.md` and
      summarised here as one table, so this file does its stated job. CLAUDE.md
      corrected where it had drifted. The checkable subset is now test-locked by
      `test_documented_paths_exist.py`: a path named in backticks in CLAUDE.md
      or `docs/architecture/` that does not exist fails the build — which is how
      the old `backend/src/domain/` claim survived so long in the one file that
      claims to override default behaviour.

   **What is already right and must not be "improved":** the dependency rule
   holds exactly (domain importing outward **0**, app importing infra **0**);
   the permanent null adapters are correct Null Object usage; ISP is not a
   problem and the ports must not be split for symmetry.

   **Out of scope:** the FROZEN aggregate (1270 lines, but "long" is not a
   capability gap), `openai_reasoner.py` (cohesive, and not while the execution
   path is moving), and performance — P8.6 owns that against its own baseline,
   and a refactor that also claims a speedup can prove neither. This is
   **behaviour-preserving in independently-green steps**; a bug found on the way
   gets its own commit, never one shared with a refactor.

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
  - Plan: `docs/history/planning/2026-08-02-phase-8-1-repository-choice-and-forge-publication.md`
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
  - Plan: `docs/history/planning/2026-08-08-phase-8-5-vm-development-environment.md`
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
- **The refactor (P8.7) is behaviour-preserving and provable**: no route, public
  API or config-key change, and each 2026-08-09 defect locked by a test that
  fails if its fix is reverted. Measured, not asserted.
- **That completion happens in a time somebody will actually wait for**, shown
  by a second measured run against the same fixture as
  `docs/history/analyses/2026-08-09-cycle-latency-analysis.md`. The baseline to
  beat is 61 minutes with zero goals promoted and 62% of execution wall-clock
  producing nothing. A cycle that is correct but unwatchable fails the phase's
  own purpose, because the artifact is meant to be *shown to someone*.

## Phase 9 — the console: refinement, refactor, and browser-proven behaviour ✅ (delivered 2026-08-10)

**Scoped 2026-08-10**, replacing the small peer preview (deleted — see the
launch-sequence note at the top). Phase 8 made the backend
demonstrable and finished by publishing a run somebody can read. The console is
now the weakest surface in the repository and the first one a visitor touches:
Phase 5 made it render backend truth instead of rebuilding transition rules,
and nothing has examined it since.

**External capability:** an operator can drive a complete cycle through the
browser, understand what the system is doing at a glance, and the console's
behaviour is proven by tests that drive a real browser rather than asserted by
unit tests around it.

### What is actually there — the starting evidence, not the conclusion

Measured 2026-08-10, so the analysis in task 1 starts from facts rather than
from a blank page. **These are observations, not yet findings** — task 1 exists
to decide which of them matter.

| | |
|---|---|
| Hand-written source | ~14,700 lines (plus 6,145 generated `types.gen.ts`) |
| Largest modules | `lib/api.ts` 906, `lib/queries.ts` 904, `GatePanel.tsx` 636, `Overview.tsx` 564 |
| Settings sections | four files of 436–513 lines each |
| Styling | 23 CSS modules + `styles/tokens.ts` + `global.css` — **and 119 inline `style={{…}}` sites** |
| Primitives | `components/ui/` already has Button, Card, Dialog, Field, Input, Select, ErrorState, ConfirmAction, CountChip, AttentionItem |
| State | `zustand` store + 35 `react-query` hooks |
| Accessibility | 182 `aria-*`/`role=` usages — awareness exists, coverage unmeasured |
| Component tests | 9 files, mostly `lib/` |
| **Browser tests** | **2 specs** — `packaged-ui`, `docs-screenshots`. Neither drives a cycle. |

The last row is the one that matters. `CLAUDE.md` has said *"Light by design —
full-cycle browser E2E is Phase 8"* since Phase 6, and Phase 8 came and went
without it. **No test in this repository has ever driven the console through a
plan.** Every claim about whether the UI works is currently inference from unit
tests and from a human clicking around.

The 119 inline styles beside a token file and 23 CSS modules is the second: it
means there are at least two styling systems in use, and a design pass that does
not first pick one will produce a third.

### The order, and why it is this order

Analysis before refactor, refactor before redesign, and **browser tests before
any of it lands**. A refactor with no browser coverage is indistinguishable from
a rewrite with unknown regressions, and this is exactly the mistake P8.7's
scoping note warns about: *"changing behaviour inside code that cannot be read
is how those defects arrived"*, and *"optimising a system whose tests cannot
fail on its real failure modes measures nothing."* The same argument applies
here with more force, because the console has no equivalent of the dual-backend
truth test.

1. ⬜ **Pin the agent-driven Playwright tooling, and write the safety net
   first.** The tool is **`npx playwright cli`** — the terminal-driven browser
   built into `playwright-core`, which `@playwright/test` 1.62.1 already brings
   in. It opens a browser, acts on it (`click`, `fill`, `press`, `select`),
   and returns an accessibility-tree `snapshot` with stable element refs, plus
   `find`, `eval`, `requests`/`response-body` and `screenshot`. Its own agent
   skill ships at
   `node_modules/playwright-core/lib/tools/skills/playwright-cli/SKILL.md`.

   **Deliberately NOT the MCP server.** `playwright mcp` exists as a subcommand
   and the standalone `@playwright/mcp` package exists too; both were tried and
   rejected on 2026-08-10. A resident server speaking a protocol is expensive
   per interaction and slower than a shell command that prints a snapshot and
   exits. The CLI is the same capability at a fraction of the cost, and it
   composes with ordinary shell tooling.

   Pin the version **exactly** (no caret) in `package.json` and record it — an
   agent-driven test tool that silently updates is a test suite that silently
   changes meaning. Note the CLI defaults to branded Chrome; point it at the
   bundled chromium with a committed
   `.playwright/cli.config.json` (`{"browser":{"browserName":"chromium"}}`) so
   it uses the same browser CI installs rather than requiring a second one.

   Then, **before touching a single component**, write the browser tests for
   what exists today. They are the regression net for everything after, and
   writing them first is the only way to know they test the current behaviour
   rather than the behaviour we are about to write.

   Coverage must include, at minimum, the flows an operator cannot avoid:
   - the plan list and the composer — creating a project and a plan;
   - the intent gate: read the proposal, approve, edit, cancel;
   - the cycle-draft gate, including the goals canvas;
   - execution in flight — the SSE feed updating status without a reload;
   - the per-goal review surface and its diff view;
   - the publication gate and each of the four dispositions;
   - block resolution, and the settings flows for providers, agents and runner.

   These run against a real API with a staged bundle, at **Tier 0 (stub + dry
   run)** so they are deterministic and CI-lockable. A browser test that needs a
   paid model is a demo, not a test — that distinction is `demos/README.md`'s
   and it applies here unchanged.

2. ⬜ **Full frontend analysis, written down before anything changes.** Produce
   an analysis document the way P8.6 and P8.7 were scoped — from measurement,
   naming files and line numbers, so the refactor plan is derived rather than
   asserted. It must cover:
   - **Responsibility boundaries.** Where do data fetching, view state, domain
     interpretation and presentation currently live, and where do they leak into
     each other? `lib/planTruth.ts` and `lib/setupPlan.ts` suggest the seam
     already exists in places; the 500-line settings sections suggest it does not
     everywhere.
   - **SOLID, concretely.** Single responsibility is the one with teeth here
     (636-line `GatePanel` handling three different gate subjects; 14 `useState`
     in one settings section). Dependency inversion matters at the API boundary —
     components should depend on a typed port, not on `fetch` shapes. Interface
     segregation matters for prop objects that carry a whole plan into a leaf.
     **Do not apply the acronym decoratively**: each finding must name the file,
     the concrete cost, and what breaks today because of it.
   - **DRY, honestly.** Real duplication (the same gate-approval POST shape
     written four times) is worth removing; incidental similarity is not.
     Premature deduplication is how a 500-line component becomes a 500-line
     component with an unusable abstraction on top.
   - **Which design patterns actually fit.** Candidates to evaluate, not to
     adopt on sight: compound components for the gate/dialog family, a
     reducer or state machine for multi-step forms, container/presenter for the
     views, a single query-key factory for the 35 react-query hooks, and one
     styling system with the inline styles migrated into it. **Reject any
     pattern that does not remove a named problem from the analysis.**
   - **The generated-types boundary.** `types/generated/` is regenerated from
     OpenAPI, and `types/ui.ts` hand-declares the plan detail read model. That
     split is deliberate; the analysis should confirm it is still holding rather
     than quietly drifting.

3. ⬜ **A refactoring plan, then execute it in reviewable steps.** Same shape as
   P8.7, which is the precedent: behaviour-preserving, provably so, and split so
   each step is separately reviewable. No route, no visible behaviour, and no
   API contract changes in this task — the browser tests from task 1 are the
   proof, and they must stay green at every step rather than only at the end.

4. ⬜ **UI/UX refinement: layout and design.** Only now, on code that can be
   read and behaviour that is pinned. Grounded in the ordinary practices rather
   than in taste:
   - **One visual system.** A single source of spacing, type scale, colour and
     elevation, with the 119 inline styles migrated into it. Light and dark
     both, since the console already ships a theme.
   - **Visual hierarchy that matches the domain.** The most important question
     an operator has is *what is this plan waiting for and what can I do about
     it* — the backend already answers it with `status`, `status_reason`,
     `activity` and `legal_actions`, and the layout should make that answer the
     loudest thing on the page.
   - **States are designed, not defaulted.** Empty, loading, error, partial and
     stale each get a designed treatment. `ErrorState` exists; whether every
     surface uses it is an open question.
   - **Accessibility as a requirement.** Keyboard paths for every gate action,
     visible focus, labelled controls, sufficient contrast, and correct roles on
     the dialogs and the canvas. 182 `aria-*` usages is a starting point, not a
     result — measure it.
   - **Responsive down to a laptop viewport**, which is the machine this runs on.
   - **Latency and feedback.** Long actions are the norm here; optimistic states
     and progress must not claim more than the backend has said.

5. ⬜ **Meticulous browser E2E, driven by the agent tooling, with screenshots.**
   Extend task 1's net into the full matrix, and use screenshots as *evidence*
   rather than decoration: capture each major surface in both themes, attach
   them to the run, and diff them across the refactor so a visual regression is
   caught rather than argued about. Screenshots also give the model doing the
   work visual feedback it cannot get from the DOM alone — which is the point of
   using the LLM-driven tooling at all.

   **A screenshot is not a passing test.** Every screenshot must sit beside an
   assertion that would fail on its own; otherwise a broken page produces a
   tidy picture of a broken page.

### Exit criteria — all met 2026-08-10

- ✅ **Tooling pinned and recorded.** `npx playwright cli` (built into
  `playwright-core`), `@playwright/test` pinned to exactly `1.62.1`, and
  `.playwright/cli.config.json` pointing it at the bundled chromium. **Not**
  the MCP server — both forms were tried and rejected on cost.
- ✅ **Browser tests drive every flow, at Tier 0, in CI.** `e2e/cycle/` — 11
  specs covering composer, discovery, both gates plus the edit path, execution
  over SSE, publication, all four dispositions, every settings section, the
  plan tabs and the manual. A whole cycle runs in about six seconds.
- ✅ **The analysis names files and line numbers**
  ([`docs/history/analyses/2026-08-10-frontend-analysis.md`](docs/history/analyses/2026-08-10-frontend-analysis.md))
  and records its own outcome, including the two defects it MISSED that the
  refactor found.
- ✅ **The refactor is behaviour-preserving and was green at every step**, in
  four separately reviewable commits.
- ✅ **One styling system.** 119 → 66 inline styles; the survivors are React
  Flow's documented exception plus a handful of one-liners.
- ✅ **Accessibility measured before and after.** axe-core at WCAG 2.1 A+AA
  over eight real surfaces: **6 violations → 0**, and the measurement now runs
  in the suite so it cannot silently regress.
- ✅ **Screenshots of every major surface in both themes**, each beside an
  assertion that would fail on its own, attached to the run and uploaded as a
  CI artifact.

**What it actually found.** The phase was scoped from line counts, and the
measurement contradicted three of those assumptions outright — `api.ts`, the
query-key factory and `GatePanel` were all fine, and the analysis says so
rather than refactoring them anyway. What was genuinely wrong was invisible
from a line count and mostly invisible from reading:

- a `<button>` containing two `role="button"` spans, whose accessibility tree
  collapsed to one control named `"AGENT EVENTS · 1 FAILED ONLY"`;
- **six WCAG failures, every one in the light theme** — the theme nobody had
  been looking at — including a goals canvas that floated a hardcoded DARK
  panel under theme-following light text at **1.54:1**;
- a phase-timeline highlight that never rendered at all, because it
  concatenated hex alpha onto a `var()`;
- a minimap still painted dark in light mode, caught by *looking at the task 5
  screenshots* — which is the entire argument for taking them;
- two plan tabs with no heading, and a composer that could only ever create the
  FIRST project.

Every one is locked by a test.

### What this phase must not do

- **No backend changes.** If the console needs something the API does not
  serve, that is a finding for Phase 10, not a licence to widen the API here.
- **No domain un-freeze.** Nothing in a UI refactor justifies one.
- **No new features.** Refinement of what exists. A missing capability is
  Phase 10's evidence to collect.
- **No redesign before the tests.** Stated three times on purpose.

## Phase 10 — repository audit, then launch ⬜ (current)

**Scoped 2026-08-10.** Two halves that look unrelated and are not: nobody should
launch a system they have not audited, and an audit with no launch behind it is
procrastination. Do them in this order.

### 10A — audit the whole repository, and prove every claim

**External capability:** a written, evidence-backed account of what is actually
wrong with this system, which is the thing you need before inviting anyone.

Sweep the repository for design flaws, gaps, edge cases and bugs — backend,
frontend, infra, fixtures, docs. The single rule, and it is not negotiable:

> **A finding is only a finding with concrete proof. Never an assumption.**

Proof means one of: a failing test that passes after the fix; a reproduction
with the exact commands and observed output; a log or ledger row from a real
run; or a citation of the code path with the specific inputs that reach it. A
plausible-sounding reading of the code is a *hypothesis*, and hypotheses go in a
separate list from findings.

**The precedent is already in this repository, and it is the standard to
meet.** P8.6's Task 1 — "an empty completion is misclassified as a rate limit" —
was scoped, written down, and then **retracted before implementation** because
the evidence turned out to compare two different models. Implementing it would
have made the runtime impatient with genuine 429s, which is the exact harm the
patient curve exists to prevent. That retraction is worth more than the fix
would have been, and this phase should expect to produce several like it.

- Findings go to `docs/architecture/known-issues.md` **with their proof**, which
  is what that file already requires; fixing one means deleting the entry and
  adding the regression test that locks it.
- Hypotheses that cannot be proven get recorded as hypotheses, with what
  evidence would settle them — the P8.6 Task 5 treatment ("not reproduced, and
  not reachable by that run's shape", deliberately left open) rather than
  silent deletion.
- Expect the audit to produce more retractions than fixes. That is a healthy
  result, not a failed phase.

Areas that have never been swept, in rough priority: error paths and edge cases
in the API surface; concurrency around the lease and goal-lease interaction
under real contention; migration/upgrade paths on an existing install; secret
handling and the auth surface; the frontend's error and stale-data states
(post-Phase-9); and every place a doc and the code could have drifted.

**Sweep 1 — done 2026-08-10** (auth, secrets, validation, migrations):
[`docs/history/analyses/2026-08-10-phase-10a-audit-sweep-1.md`](docs/history/analyses/2026-08-10-phase-10a-audit-sweep-1.md).
Five findings, all proven and all fixed with regression tests; three areas
verified clean; one hypothesis retracted before it reached the findings list.
The two that matter: the API's own `/api/openapi.json`, `/api/docs` and
`/api/redoc` answered **anonymously** with a token set — invisible to the
guard's parametrized sweep because FastAPI marks them `include_in_schema=False`
— and a 422 echoed the **submitted plaintext `api_key`** back to the caller,
which the console then rendered into a toast. Neither was reachable by the 1472
green tests.

**Sweep 2 — done 2026-08-10** (the lease and goal-lease under real contention):
[`docs/history/analyses/2026-08-10-phase-10a-audit-sweep-2.md`](docs/history/analyses/2026-08-10-phase-10a-audit-sweep-2.md).
**No defects.** Mutual exclusion holds under every race constructed — one plan
against 16 workers, 8 plans against 16, expired-lease reclaim, live-lease theft,
and the goal lease — with zero double-claims. What it produced instead is the
coverage that was missing: the goal lease had a two-thread race, the **plan
claim had none**, and `test_plan_claim_contention.py` now drives five, each
verified capable of failing by removing the claim predicate. Plus two
retractions, one of which ("the claim path collapses under contention") was a
fixture violating `uq_plans_project_id`, not a bug. It also found **two defects
in the suite**, both tests asserting something stronger than the behaviour they
protect and neither visible while the suite was quiet: a heartbeat test that
sampled a fixed ~1s window (failed 2 runs in 4 under the added load; now waits
for the beat it needs), and the container leak tests asserting the whole
*machine* had no acceptance container rather than that the run cleaned up after
itself — which one SIGKILLed orphan failed permanently until pruned by hand.

**Sweep 3 — done 2026-08-11** (the reasoner tool surface against hostile model
output):
[`docs/history/analyses/2026-08-11-phase-10a-audit-sweep-3.md`](docs/history/analyses/2026-08-11-phase-10a-audit-sweep-3.md).
Sweep 2 left this area *unproven* rather than clean; exercising it found two.
The package already treats model output as untrusted for CONTENT
(`_validate_submission`), but nothing checked output hostile in SHAPE: a single
turn's tool-call fan-out was **unbounded** (500 calls in one turn ran 500
handlers against the repository reader and grew the transcript by 500 messages),
and a handler's raw exception text — absolute paths included — was returned as a
tool result and therefore **sent to the provider** on the next request. Both
fixed; the fan-out excess is refused rather than dropped, because these
providers require a tool message per `tool_call_id`.

**Sweep 4 — done 2026-08-11** (the console's error and stale-data states, and
doc/code drift):
[`docs/history/analyses/2026-08-11-phase-10a-audit-sweep-4.md`](docs/history/analyses/2026-08-11-phase-10a-audit-sweep-4.md).
**The console came out clean on every property checked** — the backend's 29
domain events and the client's listener list match exactly in both directions
(a name with no listener is silently dropped, so this is the one that would rot
invisibly), every listened event reaches a cache invalidation, the reconnect gap
resyncs, and the views branch on `error` with a retry. The drift half found the
last one: two source comments naming paths that do not exist, one of them
`backend/src/api/security.py` — the never-existed layout
`test_documented_paths_exist.py` was written about, still repeated in current
code two refactors later. That test now covers source comments too.

**10A's area list is complete: 11 findings, all proven and all fixed with a
regression test; 4 retractions; nothing on the original list unswept.** What
remains before the phase closes is a judgement call rather than a sweep — see
the exit criteria below.

### 10B — the launch

**External capability:** the project is findable, understandable and installable
by someone who has never heard of it, under a name that does not need
explaining.

**The rename comes first, because everything else bakes it in.** `aipom` is a
Pokémon, which is charming and unsearchable, collides with an existing name, and
tells a visitor nothing. Choose a replacement deliberately:

- Check availability across **PyPI, npm, GitHub org, and the domain**, and check
  for trademark collision. A name available in three places out of four is not
  available.
- Prefer something that says what it does. The positioning at the top of this
  file — *local-first, human-gated, verified multi-agent coding orchestrator* —
  is the brief.
- **Scope the rename honestly before committing to it.** It touches the Python
  package `agent_orchestrator`, the CLI entry point `orchestrate`, the dev guest
  `aipom-dev` and `infra/dev-vm/`, the acceptance container prefix
  `aipom-acceptance-*`, the state directory `~/.orchestrator` and
  `ORCHESTRATOR_*` environment variables, the docs, and every fixture. Some of
  those are user-visible state on existing installs and need a migration or a
  compatibility alias, not a `sed`.

Then the campaign, written the way a marketing team that has shipped developer
tools would write it rather than the way engineers imagine marketing works:

- **Positioning and message.** One sentence a developer repeats correctly after
  hearing it once. The honest differentiator is already in this repository and
  is unusual: *the orchestrator records the boundary between the test proven RED
  and the implementation that made it GREEN, and nothing else does.* Lead with
  the verification story, because "AI writes code" is not a claim anyone is
  short of.
- **Audience and channel.** Who specifically — developers running agents on
  their own repositories who do not trust the output. Where they actually are.
  What each channel's norms are; a launch post that reads as an ad on a forum
  that hates ads is worse than no post.
- **The assets.** Landing page, README as a sales page (it is the real landing
  page for a developer tool), a short demo video built from the
  `static-site-v1` run, the Phase 9 screenshots, and the evidence documents —
  which are a genuine asset almost nobody else has, because they show a red run
  published rather than retried.
- **Proof over promises.** Point at `demos/static-site-v1/runs/`. A published
  run with its failures visible is more persuasive to this audience than any
  claim, and it is already written.
- **Sequencing and metrics.** What ships in what order, what each channel is
  expected to produce, and what result would mean *stop and fix the product
  instead of marketing it harder*. Decide that threshold before launching, not
  after.
- **The support path and the feedback loop.** Both were deleted with the peer
  preview because they had no audience; both become real here, designed against
  the audience the launch actually produces rather than an imagined one. A
  support channel with nobody in it is a maintenance cost — build it when the
  first invitation goes out, not before.

### Exit criteria

- Every audit finding carries proof; every hypothesis is labelled as one.
- `known-issues.md` reflects the audit, and every fix landed with a regression
  test.
- A name is chosen, verified available across all four surfaces, and the rename
  is scoped with a migration path for existing installs.
- A written launch plan with positioning, channels, assets, sequencing, metrics,
  and a pre-agreed "stop and fix the product" threshold.
- The support path exists before the first invitation goes out.

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
