# The second measured run

**2026-08-10.** The rerun the P8.6 plan asks for, measured against
[`2026-08-09-cycle-latency-analysis.md`](2026-08-09-cycle-latency-analysis.md)
by the same method: attempts classified by terminal status, idle taken as the
gaps between them.

`demos/static-site-v1`, plan `aaedbb73`, cycle `a0e0d14c`, orchestrator
`4f3d802`. Reasoner `deepseek/deepseek-v4-flash-0731` (paid, OpenRouter);
agents `codex-test` / `codex-dev` on the `codex` CLI against a ChatGPT
subscription. Captured in
[`demos/static-site-v1/runs/20260810T133717Z-aaedbb73/`](../../../demos/static-site-v1/runs/).

## The headline

The cycle **completed**. Four goals, all promoted, publication gate reached,
disposition recorded, root back to IDLE.

| | 2026-08-09 | 2026-08-10 |
|---|---|---|
| Wall-clock | 61 min, still going | **13m 37s**, finished |
| Goals promoted | **0** of 5 | **4** of 4 |
| Attempts | 15 (9 recorded) | 8 |
| Attempts that failed | 6 of 9 | **0 of 8** |
| Productive share | 38% | **78.5%** |
| Wasted on failed attempts | 20% | **0%** |
| Idle in backoff | 42% | 21.5% (see below) |
| Cost | $0 (free tier) | **$0.013** |

Of the 21.5% idle, **all but 44 seconds was me**: a 102.7s gap while I paused
the plan to restart a worker (below). The two remaining gaps — 18.7s and
24.7s — are the JIT enrichment sessions for the next goal, which is planning
work rather than waiting. **Genuine idle in this run is approximately zero.**
Every other attempt began within 0.6s of its predecessor finishing.

Excluding the operator pause: 92.4% productive, 0% wasted, ~7.6% spent
enriching the next goal.

## Do not read this as a 4.5x improvement from the P8.6 code

The dominant term is the one the plan predicted, and it is not the code in this
phase. **The free tier was removed**, on both sides at once:

| | 2026-08-09 | 2026-08-10 |
|---|---|---|
| Reasoner | `poolside/laguna-s-2.1:free` | `deepseek/deepseek-v4-flash-0731` (paid) |
| Agents | free OpenRouter models | `codex` CLI, ChatGPT subscription |

Six of nine attempts in the baseline failed with genuine upstream 429s and
`RESOURCE_EXHAUSTED`. **Zero attempts failed here, for any reason.** Intent
discovery went from 13m04s to 12s; that is a different provider answering, not
a smarter orchestrator. The run also decomposed into 4 goals rather than 5, so
even the goal counts are not directly comparable.

What can honestly be attributed to this phase:

- **Parallel enrichment (Task 2) fired and is visible in the ledger.** Goals
  `8e179f13` and `5414e50b` — the two with no dependencies — committed their
  contracts at 13:13:52 and 13:14:00, **8 seconds apart, from one pass**.
  Serially the second session would not have started until the first
  committed. Goals 3 and 4 were still enriched one at a time, correctly: each
  depends on earlier goals, and JIT enrichment must not freeze a contract
  against a tree its dependency has not written yet. The win scales with how
  many goals are independent, and this brief only had two.
- **Unbuffered worker logs (Task 4.1) did their job.** Every diagnosis in this
  session came from reading the live log, which is the thing that was
  impossible on 2026-08-09.
- **Capacity routing (Task 3) was never exercised.** Nothing was rate-limited,
  no circuit opened, so the reroute path did not run once. It is locked by
  tests, not by this run, and this run says nothing about whether it helps.

## Task 5: the 31-minute gap did not reproduce

The baseline recorded `retry_at` armed at 18:45:47 and the next attempt at
19:16:37, with a live renewing lease and no log in between. Nothing resembling
it appeared here. The complete set of gaps between attempts:

```
  0.0s   18.7s   102.7s   0.6s   0.3s   24.7s   0.0s
```

The 102.7s is an operator pause and restart. The 18.7s and 24.7s are enrichment
sessions. There is no unexplained gap of any size.

This is not a proof of absence: no attempt in this run ever entered backoff, so
the code path the ghost lived on — an armed `retry_not_before` being honoured
late — was never taken. **The honest status is "not reproduced, and not
reachable by this run's shape"**, not "fixed". It should be looked for again
the next time a run actually backs off, and closed only then. Recorded here
rather than left as folklore, per the plan's own instruction.

## What the run cost

**$0.013** for the whole cycle, against a $1 cap: about **77 complete runs per
dollar**. The plan's estimate for this model was ~$0.021 (~48 runs); the real
figure is better, because the plan extrapolated from 7 planning sessions and
this cycle needed 6.

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
says not to raise it to go faster. So this run demonstrates the enrichment half
of P8.6's parallelism and cannot demonstrate the execution half. A roster on
token-billed providers would.

## Defects this run surfaced

All four were fixed in flight with regression tests; none was in the
orchestration core.

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
that found it**, exactly as `demos/README.md` claims it would, and it is now
encoded so the next run does not need eyes to catch it.

## The container acceptance run

Four verdicts, all from a real `python:3.12-slim` container against the
assembled cycle tree:

| Trigger | Outcome | |
|---|---|---|
| goal_merge (frontmatter) | failed | `No module named sitegen.cli` |
| goal_merge (markdown) | failed | `No module named sitegen.cli` |
| goal_merge (cli) | **passed** | booted, all 5 scenario steps |
| pre_publication | **passed** | booted, all 5 scenario steps |

The two failures are correct and advisory: the CLI did not exist yet at those
points. Nothing gated on them, no container leaked (`docker ps -a` clean after
each), and teardown ran on both the passing and failing paths — P8.5's
adapter doing exactly what it was built to do, now on a real multi-goal cycle
rather than a test.
