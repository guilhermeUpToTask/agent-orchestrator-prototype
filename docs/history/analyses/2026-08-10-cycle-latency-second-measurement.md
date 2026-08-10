# The second measured run

**2026-08-10.** The rerun the P8.6 plan asks for, measured against
[`2026-08-09-cycle-latency-analysis.md`](2026-08-09-cycle-latency-analysis.md)
by the same method: attempts classified by terminal status, idle taken as the
gaps between them.

`demos/static-site-v1`. Reasoner `deepseek/deepseek-v4-flash-0731` (paid,
OpenRouter); agents `codex-test` / `codex-dev` on the `codex` CLI against a
ChatGPT subscription.

**Two runs, and the second is the one to quote.** Run 1 (plan `aaedbb73`,
orchestrator `4f3d802`) completed 4 goals but included a 103-second operator
pause to restart a worker, and surfaced four defects. Run 2 (plan `d098aece`,
orchestrator `fe6d54f`) was the rerun after those fixes: **5 goals — the same
count as the baseline — uninterrupted.** Captured in
[`20260810T133717Z-aaedbb73/`](../../../demos/static-site-v1/runs/20260810T133717Z-aaedbb73/)
and
[`20260810T164908Z-d098aece/`](../../../demos/static-site-v1/runs/20260810T164908Z-d098aece/).

## The headline

Both cycles **completed** — every goal promoted, publication gate reached,
disposition recorded, root back to IDLE. Against a baseline that reached 61
minutes with zero goals promoted.

| | baseline 2026-08-09 | run 1 | **run 2** |
|---|---|---|---|
| Goals | 5 | 4 | **5** |
| Wall-clock | 61 min, still going | 13m 37s | **13m 03s**, finished |
| Goals promoted | **0** | 4 of 4 | **5 of 5** |
| Attempts / failed | 9 recorded, 6 failed | 8, 0 failed | **10, 0 failed** |
| Productive | 38% | 78.5% | **92.5%** |
| Wasted on failed attempts | 20% | 0% | **0%** |
| Idle | 42% | 21.5% | **7.5%** |
| Cost | $0 (free tier) | $0.013 | **$0.0134** |

Run 2 is the honest comparison: same goal count as the baseline, no operator
interruption. Its 7.5% "idle" is two gaps of 21s and 30s, each the JIT
enrichment session for the next goal — planning work, not waiting. **Genuine
idle is zero**; every other attempt began within a second of its predecessor
finishing. (Run 1's 21.5% was almost entirely the operator pause; excluding it,
92.4% productive — which run 2 then reproduced without needing the caveat.)

Ten attempts, five goals, **exactly two per goal and every one succeeded** —
the TDD shape working as designed: a RED test commit, then the implementation
that makes it GREEN.

## Do not read this as a 4.7x improvement from the P8.6 code

The dominant term is the one the plan predicted, and it is not the code in this
phase. **The free tier was removed**, on both sides at once:

| | 2026-08-09 | 2026-08-10 |
|---|---|---|
| Reasoner | `poolside/laguna-s-2.1:free` | `deepseek/deepseek-v4-flash-0731` (paid) |
| Agents | free OpenRouter models | `codex` CLI, ChatGPT subscription |

Six of nine attempts in the baseline failed with genuine upstream 429s and
`RESOURCE_EXHAUSTED`. **Zero of eighteen attempts failed across the two runs
here, for any reason.** Intent discovery went from 13m04s to 12s; that is a
different provider answering, not a smarter orchestrator.

The reasoner also decomposed the same brief differently each time — 4 goals in
run 1, 5 in run 2 (splitting HTML escaping into its own goal) — which is the
demo working as designed and a reminder that no two runs are the same
experiment.

What can honestly be attributed to this phase:

- **Parallel enrichment (Task 2) fired in both runs and is visible in the
  ledger.** Run 1: goals `8e179f13` and `5414e50b`, the two with no
  dependencies, committed contracts 8 seconds apart from one pass. Run 2:
  `afd55172` and `0e3800ae`, 4 seconds apart. Serially the second session would
  not have started until the first committed. The remaining goals were still
  enriched one at a time, correctly — each depends on earlier goals, and JIT
  enrichment must not freeze a contract against a tree its dependency has not
  written yet. The win scales with how many goals are independent, and this
  brief only ever had two.
- **Unbuffered worker logs (Task 4.1) did their job.** Every diagnosis in this
  session came from reading the live log, which is the thing that was
  impossible on 2026-08-09.
- **Capacity routing (Task 3) was never exercised, in either run.** Nothing was
  rate-limited, no circuit opened, so the reroute path did not run once. It is
  locked by tests, not by these runs, and these runs say nothing about whether
  it helps. Both agents sit on one subscription seat with no sibling provider
  to route to, so this roster could not have exercised it even under load.

## Task 5: the 31-minute gap did not reproduce

The baseline recorded `retry_at` armed at 18:45:47 and the next attempt at
19:16:37, with a live renewing lease and no log in between. Nothing resembling
it appeared in either run. The complete set of gaps between attempts:

```
run 1   0.0s   18.7s   102.7s   0.6s   0.3s   24.7s   0.0s
run 2   0.0s    0.6s     0.0s  20.9s   0.0s    0.5s   0.0s  29.4s   0.0s
```

The 102.7s is an operator pause and restart. Every other gap above one second
is an enrichment session for the next goal. There is no unexplained gap of any
size in either run.

This is not a proof of absence. **No attempt in either run ever entered
backoff** — 18 attempts, 18 successes — so the code path the ghost lived on, an
armed `retry_not_before` being honoured late, was never taken at all. **The
honest status is "not reproduced, and not reachable by either run's shape"**,
not "fixed". It should be looked for again the next time a run actually backs
off, and closed only then. Recorded here rather than left as folklore, per the
plan's own instruction.

## What the run cost

**$0.013** and **$0.0134** for the two complete cycles, against a $1 cap:
about **75 complete runs per dollar**, and the second figure is for five goals
rather than four. The plan's estimate for this model was ~$0.021 (~48 runs);
the real figure is better, and notably flat in the goal count — the extra goal
in run 2 cost about four hundredths of a cent.

Worth correcting while it is in front of us: the plan's price table is stale for
`z-ai/glm-5.2`, which it lists at $0.07/$0.22 per Mtok. The live price is
**$0.76/$2.42** — 10x — which would have made the plan's recommended reasoner
~$0.25 a run and the $1 budget worth 4 runs rather than the ~9 it claims. The
agent side cost nothing here because `codex` bills a subscription seat, not
tokens.

## The constraint nobody should optimise away

Execution was **strictly serial**, and correctly so. The `openai-codex`
provider's model row declares `max_inflight: 1`, so the admission gate declined
to start a second attempt while one was running — visible in the run as goal 2
sitting `pending`, fully enriched and dependency-free, while goal 1 ran.

That is a deliberate, documented control: a subscription seat driven by a
parallel retry loop is what looks abusive, and `CodexRunner`'s own docstring
says not to raise it to go faster. So these runs demonstrate the enrichment half
of P8.6's parallelism and structurally cannot demonstrate the execution half. A
roster on token-billed providers would.

It also means the wall-clock here is essentially **the sum of ten agent calls**:
636.7s of agent work inside a 688.2s window, on a serial pipe. That is the real
remaining lever, and it is a provider-concurrency question rather than an
orchestration one.

## Defects run 1 surfaced

All four were fixed in flight with regression tests; none was in the
orchestration core. **Run 2 was the rerun after those fixes and surfaced
none.**

1. **`_default_branch` followed the working tree** — `symbolic-ref HEAD`
   answers "what is checked out", and for a local project with no
   `origin/HEAD` that silently redefined the branch new cycles are cut from.
   Following the demo README's own "switch to the cycle branch and look at it"
   step made `verify_demo.py` fail a guarantee that was being kept. 6/7 → 7/7
   with nothing else changed.
2. **`NoEnvironment` told a fully configured project it had configured
   nothing** — `environment.mode` is read once per process, so setting it after
   a worker starts leaves that worker on the fallback, which then blamed the
   project config. Cost a real diagnosis here.
3. **The demo's content contradicted its own brief** — both content files
   repeated the front-matter title as a body `# heading`, and the brief also
   makes the layout render that title as a visible heading, so a correct
   implementation could only emit two identical `<h1>`s. It did. Every gate
   passed: the container acceptance run booted the tool and built the site, and
   all eleven acceptance assertions held, because each asked whether something
   was *present* and none asked whether it appeared *once*.
4. **`capture-run.sh` did not exist** at the path the demo README and this
   phase's plan both named.

Number 3 is the one worth keeping. The container acceptance run passed, the
structural checks passed 7/7, the out-of-repo acceptance suite passed 11/11 —
and the page still had a visibly duplicated title. **The eye check is the check
that found it**, exactly as `demos/README.md` claims it would.

It is now encoded in three places, and run 2 passed all of them: the acceptance
suite's new `test_the_title_appears_as_exactly_one_visible_heading` (12/12,
where it fails against run 1's output), a sixth container scenario step
asserting the same thing on the assembled tree before publication, and a seed
contract test that refuses content repeating its own front-matter title. The
next run does not need eyes to catch it.

## The container acceptance run

Ten verdicts across the two runs, every one from a real `python:3.12-slim`
container against the assembled cycle tree. Run 2:

| Trigger | Outcome | |
|---|---|---|
| goal_merge ×4 | failed | `No module named sitegen.cli` |
| goal_merge (cli) | **passed** | booted, all 6 scenario steps |
| pre_publication | **passed** | booted, all 6 scenario steps |

The failures are correct and advisory: the CLI did not exist yet at those
points, and the verdict is *supposed* to say so rather than pretend. Nothing
gated on them, no container leaked (`docker ps -a` clean after every run), and
teardown ran on both the passing and failing paths — P8.5's adapter doing
exactly what it was built to do, now on two real multi-goal cycles rather than
a test.

Run 2's scenario carries a sixth step the first did not: `test $(grep -c "<h1"
…) -eq 1`. The acceptance run is the operator's to author, and this is what
that is for — a finding from looking at the product, turned into a check that
runs before publication.
