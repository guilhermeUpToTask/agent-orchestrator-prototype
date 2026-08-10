# static-site-v1 — run 2, 2026-08-10

**The clean run.** Same day as
[`20260810T133717Z-aaedbb73`](../20260810T133717Z-aaedbb73/), rerun after that
one's finding was fixed at the source: the seed content no longer repeats its
front-matter title as a body heading.

| | |
|---|---|
| Plan / cycle | `d098aece` / `9768a841` |
| Orchestrator | `fe6d54f` |
| Reasoner | `deepseek/deepseek-v4-flash-0731` (OpenRouter, paid) |
| Agents | `codex-test`, `codex-dev` — `codex` CLI on a ChatGPT subscription |
| Environment | `container` (docker), `python:3.12-slim` |
| Wall-clock | **13m 03s**, uninterrupted |
| Cost | **$0.0134** |
| Outcome | 5 of 5 goals promoted · disposition `retain_branch` · root IDLE |

## Why this one is the measurement to quote

Run 1 was 4 goals and included a 103-second operator pause while a worker was
restarted. This one is **5 goals with no interruption** — the same goal count
as the 61-minute baseline, so it compares directly.

| | baseline 2026-08-09 | run 2 |
|---|---|---|
| Goals | 5 | 5 |
| Wall-clock | 61 min, still going | **13m 03s**, finished |
| Goals promoted | **0** | **5** |
| Attempts / failures | 9 recorded, 6 failed | **10, zero failed** |
| Productive | 38% | **92.5%** |
| Wasted on failed attempts | 20% | **0%** |
| Idle | 42% | 7.5% — and all of it is JIT enrichment |

The 7.5% is two gaps, 21s and 30s, each the enrichment session for the next
goal. That is planning work, not waiting. **Genuine idle is zero**; every other
attempt began within a second of its predecessor finishing.

## What it produced

The reasoner decomposed the same brief differently from run 1 — five goals
instead of four, splitting HTML escaping into its own goal:

1. Front-matter parsing
2. HTML escaping utility
3. Markdown renderer subset *(depends on 2)*
4. Page layout wrapping *(depends on 2)*
5. CLI build command *(depends on 1, 3, 4)*

Exactly two attempts per goal, all succeeded: the test-author agent commits a
RED test, the implementer makes it GREEN. Goals 1 and 2 have no dependencies
and were **enriched concurrently in one pass**, their contracts committing four
seconds apart.

```bash
cd "$STATIC_SITE_REPO"
git switch cycle/9768a841-2c78-49bf-8f10-12c0a7a671f9
PYTHONPATH=src python -m sitegen.cli build content/ --out /tmp/site
# open /tmp/site/index.html — then switch back to main before verify_demo.py,
# which compares the default branch against the seed
git switch main
```

## The page

One `<h1>`, the front-matter title in both `<title>` and the visible heading,
headings at two levels, emphasis / strong / inline code, a real list, and
`about.md` rewritten to `about.html`. The duplicated title that run 1 produced
is gone, and three independent checks now say so rather than one:

- **By eye** — the check that found it in the first place.
- **The out-of-repo acceptance suite: 12/12**, including
  `test_the_title_appears_as_exactly_one_visible_heading`, which was added
  after run 1 and **failed against run 1's output**.
- **Inside the container**, as a sixth scenario step
  (`test $(grep -c "<h1" …) -eq 1`) — so the acceptance run now asserts it too,
  on the assembled cycle tree, before anything is published.

## Verdicts

- **Structural: 7/7** (`structural.txt`)
- **Acceptance: 12/12** (out-of-repo, held where no agent ever saw it)
- **Container acceptance run:**

  | Trigger | Outcome | |
  |---|---|---|
  | goal_merge ×4 | failed | `No module named sitegen.cli` — correct, the CLI did not exist yet |
  | goal_merge (cli) | **passed** | booted, all 6 scenario steps |
  | pre_publication | **passed** | booted, all 6 scenario steps |

  The four failures are advisory and correct. Nothing gated on them, no
  container leaked, and teardown ran on both the passing and failing paths.

## Files

| | |
|---|---|
| `manifest.json` | versions, ids, pinned models, verdicts, wall-clock, cost |
| `plan-detail.json` | the aggregate read model at completion |
| `attempts.json` | the attempt timeline |
| `evidence.json` | promotions and the acceptance-run verdicts |
| `agent-events.json` | runtime telemetry |
| `runner-status.json`, `reasoner-status.json` | the pinned bindings |
| `structural.txt` | `verify_demo.py` — 7/7 |
| `worker-log.txt` | the full worker log |

Measurement and attribution:
[`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](../../../../docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md).
