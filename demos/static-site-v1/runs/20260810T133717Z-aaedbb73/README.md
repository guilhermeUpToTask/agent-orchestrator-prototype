# static-site-v1 — run 1, 2026-08-10

The first completed run of this demo. Published as-is, green parts and red
parts together, per [`demos/README.md`](../../../README.md).

> **Superseded as the showcase by
> [`20260810T164908Z-d098aece`](../20260810T164908Z-d098aece/)**, the rerun
> after this run's finding was fixed — 5 goals, uninterrupted, nothing
> surfaced. This one is kept because it is the run that *found* something, and
> a demo directory that only keeps its clean runs is not evidence of anything.

| | |
|---|---|
| Plan / cycle | `aaedbb73` / `a0e0d14c` |
| Orchestrator | `4f3d802` |
| Reasoner | `deepseek/deepseek-v4-flash-0731` (OpenRouter, paid) |
| Agents | `codex-test`, `codex-dev` — `codex` CLI on a ChatGPT subscription |
| Environment | `container` (docker), `python:3.12-slim` |
| Wall-clock | **13m 37s** (12m 17s excluding an operator pause) |
| Cost | **$0.013** |
| Outcome | 4 of 4 goals promoted · disposition `retain_branch` · root IDLE |

## What it produced

`sitegen`, a markdown → HTML static site generator, decomposed by the reasoner
into four goals it chose itself:

1. Front-matter parser
2. Markdown renderer
3. Page layout wrapper *(depends on 1)*
4. CLI build command and link rewriting *(depends on 1, 2, 3)*

Eight attempts, **zero failures** — two per goal, which is the TDD shape: the
test-author agent commits a RED test, the implementer makes it GREEN, and each
lands as its own run on the task branch.

```bash
cd "$STATIC_SITE_REPO"
git switch cycle/a0e0d14c-8b92-41f4-bdd0-73478d13de36
PYTHONPATH=src python -m sitegen.cli build content/ --out /tmp/site
# then switch back — see "the honest part" below
git switch main
```

## The honest part

**The page had a visibly duplicated `<h1>`, and every automated gate passed
it.** The container acceptance run booted the tool and built the site, the
seven structural checks passed, and all eleven out-of-repo acceptance
assertions held — because each asked whether something was *present* and none
asked whether it appeared *once*.

The cause was the demo's own content contradicting its own brief: both content
files repeated the front-matter title as a body `# heading`, while the brief
also requires the layout to render that title as the visible heading. A correct
implementation of both requirements could only produce two identical headings.
It did. The agents were not wrong.

Fixed at the source and locked in both places after this run, so the artifacts
captured here still show the duplicate. **The eye check is what found it**,
which is the sequence `demos/README.md` argues for and the reason this demo
generates files instead of serving them.

Three other defects surfaced and were fixed in flight, none in the
orchestration core: `_default_branch` following the checked-out branch (which
made `verify_demo.py` fail a guarantee that was being kept, if you followed the
README's own "switch to the cycle branch and look" step), `NoEnvironment`
telling a fully-configured project it had configured nothing, and this capture
script not existing at the path the README named.

## Files

| | |
|---|---|
| `manifest.json` | versions, ids, pinned models, verdicts, wall-clock, cost |
| `plan-detail.json` | the aggregate read model at completion |
| `attempts.json` | the attempt timeline — what ran, on which model, when |
| `evidence.json` | promotions and the acceptance-run verdicts |
| `agent-events.json` | runtime telemetry |
| `runner-status.json`, `reasoner-status.json` | the pinned bindings |
| `structural.txt` | `verify_demo.py` — 7/7 |
| `worker-log.txt` | the full worker log for the run |

Measurement and comparison against the 61-minute baseline:
[`docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md`](../../../../docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md).

---

> **Note on hostnames (added 2026-08-11).** This run executed before the
> project was renamed. The guest hostname and the `*-acceptance-*` container
> names in `worker-log.txt` were **substituted** during the rename to remove a
> third party's trademark from the working tree. Nothing else was altered —
> timings, exit codes, commit SHAs and command lines are exactly as recorded.
