# P8.6 — refinement, and getting the demo done

**Created 2026-08-09.** Closes Phase 8. Two outcomes, in this order: the demo
completes and is captured, then the latency work is measured against a baseline
that already exists.

**Baseline to beat:** [`docs/history/analyses/2026-08-09-cycle-latency-analysis.md`](../../history/analyses/2026-08-09-cycle-latency-analysis.md)
— 61 minutes, zero goals promoted, 62% of execution wall-clock producing
nothing.

## Read this first: the demo has never had a fair run

Three defects sat in the path on 2026-08-09, and the run that exposed them was
also fighting a free tier that returns empty completions. **No conclusion about
the orchestrator's capability can be drawn from that run**, and none is drawn
here.

| Defect | Effect | State |
|---|---|---|
| Implementer role bound to a test-author agent | Implementation was *impossible*, not poor | ✅ fixed, `f71c843` |
| `converse`/`architect_cycle` reserved no submit turns | Planning could not submit at all | ✅ fixed, `5057ef4` |
| Empty completion classified as `rate_limit` | 1-hour wait per attempt for a limit that does not exist | ⏳ **Task 1** |

What *did* work once the first two cleared, and is worth protecting: cycle
architecture decomposed the brief into five correct goals in **23 seconds**;
test authoring produced real tests with frozen RED evidence; two agent attempts
succeeded; two goals executed in parallel; every failed attempt discarded its
worktree leaving zero trace; pause was graceful.

## Spend money before optimising anything

The free tier is the single largest confound and the cheapest to remove.
Measured token consumption from the real run: **73,264 prompt + 35,648
completion tokens** across 4 planning sessions, and 6.9 MB of agent transcript
across 15 attempts.

Extrapolated to a full five-goal cycle (7 planning sessions + ~20 agent
attempts, retries included), a paid run costs roughly:

| Model | in / out per Mtok | est. per full cycle | runs per $1 |
|---|---|---|---|
| `qwen/qwen3.7-flash` | $0.03 / $0.13 | ~$0.06 | ~17 |
| `openai/gpt-oss-120b` | $0.037 / $0.17 | ~$0.07 | ~14 |
| `z-ai/glm-5.2` | $0.07 / $0.22 | ~$0.11 | ~9 |
| `deepseek/deepseek-v4-flash-0731` | $0.09 / $0.18 | ~$0.12 | ~8 |

**$1 buys 8–17 complete cycles.** Budget is not the constraint; reliability is.
A failed run costs an hour of wall-clock and a rerun — orders of magnitude more
than the tokens. **Do not pick the cheapest. Pick the most reliable tool-caller
in the cheap tier.**

### Recommended binding

- **Reasoner:** `z-ai/glm-5.2` — 1M context, `structured_outputs`, and the only
  candidate advertising `parallel_tool_calls`; described for long-horizon
  agentic use. Planning quality gates everything downstream, so this is where
  the extra cent per run belongs.
- **Implementer + test-author agents:** `deepseek/deepseek-v4-flash-0731` —
  coding-oriented, 1M context, `structured_outputs`, $0.09/$0.18.
- **Fallback if either disappoints:** `openai/gpt-oss-120b` (cheaper, 131k
  context, explicitly agentic).

Keep **one** free model registered as a `cheap` tier agent so the routing work
in Task 3 has something to route to, but never as the reasoner.

---

## Task 1: ~~an empty completion is not a rate limit~~ — VOID

**Retracted 2026-08-09 before implementation.** The premise was wrong.

Every `rate_limit` attempt across both demo runs carries a real upstream
provider code — `429` ("`google/gemma-4-31b-it:free` is temporarily
rate-limited upstream") and `RESOURCE_EXHAUSTED` ("Worker local total request
limit reached"). No attempt was ever classified as capacity without one.

The mistake: a direct probe of `nvidia/nemotron-…:free` returned HTTP 200 and
was compared against a failure produced by `google/gemma-4-31b-it:free`. Two
different models. The `content: []` in the transcript was the consequence of a
provider refusing, not a separate misdiagnosis.

**Implementing this would have been actively harmful** — it would make the
runtime impatient with genuine 429s, which is exactly what the patient
rate-limit curve exists to prevent. The 42% idle share is real free-tier
exhaustion; the answers are Task 3 (route to a model that is not exhausted) and
a paid tier, not a classification change.

Recorded in `docs/architecture/known-issues.md` under *Failure classification*
so the reasoning error is not repeated.

## Task 2: enrich ready goals in parallel — ✅ DONE 2026-08-10

**Why:** the largest structural win, and provider-independent. Enrichment is JIT
*and* strictly serial — goal 2's session starts the second goal 1's commits — so
five goals cost ~25 minutes of pure sequencing.

- Files: `backend/praxis_orchestrator/app/handlers/planning_handler.py`,
  `backend/tests/unit/orchestration/`

**Steps**

1. Failing test: a cycle with N independent ready goals issues N enrichment
   operations without waiting for the first to commit, bounded by
   `max_concurrent_goals`.
2. Use the existing `ready_goal_ids(goals, now)` — it already computes the
   parallelism-safe set and the execution loop already honours it. Do NOT
   invent a second readiness rule.
3. Guard the invariant that makes JIT enrichment safe: a goal whose
   dependencies are unmet is still never enriched early.
4. Run the dual-backend orchestration suite (fakes AND real SQLite).

**Done when** enrichment wall-clock for a five-goal cycle is bounded by the
slowest session rather than their sum.

## Task 3: route around a busy model instead of waiting on it — ✅ DONE 2026-08-10

**Why:** the roster carries four implementers across four models; a rate-limited
task waits on its own binding while three sit idle.

- Files: `backend/praxis_orchestrator/app/handlers/execution_handler.py`

**Steps**

1. Failing test: a task whose bound agent's model is rate-limited is retried on
   the next tier-ordered capability-satisfying agent whose provider is free.
2. Reuse the existing selection path (un-freeze #16, `AgentSpec.model_role`
   tiering). **Must not mutate the persisted binding** — routing is a
   per-attempt decision, exactly as the existing admission-gate routing is.
3. Assert the binding on disk is unchanged after the rerouted attempt.

**Done when** a capacity failure costs one reroute rather than one backoff.

## Task 4: two cheap wins — ✅ DONE (code landed earlier; locked by tests 2026-08-10)

1. **Unbuffer the supervised worker.** `serve` runs the worker as a subprocess
   whose stdout is not a tty, so its log sits frozen at the startup banner while
   attempts run — this cost real diagnosis time. Pass `PYTHONUNBUFFERED=1` (or
   `-u`) in the supervisor. No design work.
2. **`verify_demo.py --seed-tag` defaults to `demo-seed`**, but
   `materialize.sh` tags `static-site-v1-seed`. Following the README works
   because it passes the flag explicitly; omitting it fails confusingly. Change
   the default.

## Task 5: diagnose the 31-minute gap — ⚠️ NOT REPRODUCED 2026-08-10, left open

Attempt 3 armed `retry_at` 18:45:47; attempt 4 began 19:16:37, with the worker
holding a live renewing plan lease and logging nothing in between. **Not** the
same as a long backoff — the arming timestamp was not honoured.

Do this AFTER Task 4.1, because unbuffered worker logs are what make it
diagnosable at all. If it reproduces, it dwarfs Tasks 1–3. If it does not
reproduce once Task 1 lands, say so and close it — a ghost that cannot be
reproduced after a related fix should be recorded as such, not left as folklore.

## Task 6: run the demo to completion and capture it — ✅ DONE 2026-08-10

Only after Tasks 1–4. Tier 1, paid models per the binding above.

**Steps**

1. `./demos/static-site-v1/scripts/materialize.sh`; confirm the seed still does
   not contain the answer (`seed/src/sitegen/__init__.py` is a docstring only).
2. `./fixtures/first-cycle-v1/scripts/preflight.sh` — must show Tier 1 pinned.
3. Fresh project + plan, brief posted verbatim, drive both gates.
4. Let execution run. Record wall-clock per phase for the rerun comparison.
5. Record the publication disposition.
6. **Check the product first, by eye**: build the site out of the cycle branch
   and open `index.html`. This is the check no evidence document can give.
7. **Then** the out-of-repo acceptance check:
   `SITEGEN_REPO=… uv run pytest demos/static-site-v1/acceptance -q`.
8. **Then** the structural check: `verify_demo.py --plan-id … --cycle-id …
   --repo … --seed-tag static-site-v1-seed`.
9. Capture with `demos/static-site-v1/scripts/capture-run.sh`, recording
   reasoner model, agent model, orchestrator version, wall-clock and cost.

**Done when** `demos/static-site-v1/runs/<UTC>-…/` exists and a human has opened
the produced HTML.

## Task 7: the second measured run, and the truth about it — ✅ DONE 2026-08-10

Re-measure against the baseline document and write the comparison: phase
wall-clock, the productive/wasted/idle split, and attempts per successful task.

**A red result gets published, not retried.** If the demo still cannot finish on
paid models, that is the finding, and it is a more valuable one than a green run
— it would mean the cost is in the orchestration rather than the provider.

## Exit criteria — all met 2026-08-10

- ✅ **The demo completes, is captured, and a human has opened the HTML.**
  Twice. Run 1: 4 of 4 goals in 13m37s for $0.013
  (`runs/20260810T133717Z-aaedbb73/`) — opening the HTML is what found the
  duplicated `<h1>` that every automated gate had passed. Run 2, after that fix:
  **5 of 5 goals in 13m03s for $0.0134, uninterrupted, nothing surfaced**
  (`runs/20260810T164908Z-d098aece/`).
- ✅ **A second measured run shows the productive share materially above the
  38% baseline, published rather than asserted** — **92.5%** on the same goal
  count as the baseline, 0% wasted, 0 failed attempts of 10, in
  [`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](../../history/analyses/2026-08-10-cycle-latency-second-measurement.md).
  That document leads with the attribution rather than the number: the free
  tier was removed on both sides, which dominates everything this phase's code
  did.
- ✅ **Every fix is locked by a regression test; no claim rests on a single
  observed run.** Including the four defects the run itself surfaced. Task 3's
  routing rests ENTIRELY on its tests — the run never rate-limited once, so it
  never exercised that path, and the analysis says so.

**One thing is deliberately left open**: Task 5's 31-minute gap did not
reproduce, but no attempt in this run entered backoff, so the path it lives on
was never taken. "Not reachable by this run's shape" is not "fixed".
