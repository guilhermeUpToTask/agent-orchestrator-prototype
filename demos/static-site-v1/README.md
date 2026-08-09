# static-site-v1 — the showcase demo

**One realistic multi-goal project, built on real models, captured.** This is
the artifact an invitation points at.

Read [`../README.md`](../README.md) first if you have not: **a demo is not a
fixture.** It runs once and is captured; it is never locked in CI; a red run is
published rather than retried.

## What gets built

`sitegen`, a small static site generator. Markdown with a front-matter header
goes in; a browsable HTML site comes out.

```
content/index.md   ──▶  sitegen build  ──▶   out/index.html
content/about.md                             out/about.html
```

The brief ([`brief.txt`](brief.txt), postable verbatim) asks for four things: a
front-matter parser, a markdown-subset renderer, a page layout, and the
`sitegen build` command with link rewriting. **How that becomes goals and tasks
is the reasoner's decision, and it decides differently every run** — that is
the point of running it on a real model, and the reason nothing here asserts a
goal count.

## Why a file generator, and not a web app

The obvious showcase is a web application. It is the wrong one right now, and
for a specific reason worth stating plainly.

The orchestrator can prove *this command exited 0 against this commit*. Proving
*the application works* is the cycle acceptance run, whose container adapter
**shipped in P8.5 on 2026-08-09** and is no longer blocked. This demo keeps its
generator shape anyway, and the original reasoning is worth preserving: it was
chosen while the adapter did not exist, because a web app's goals would have
ended as *"tests passed, nobody can tell whether it runs"* — exactly the gap
Phase 8 exists to close. A generator needs no adapter to be checkable by eye,
which is a better property for a showcase than a dependency on one.

A generator has no such gap. **The demo ends with a file you open in a
browser.** A human confirms the product works with no container and no trust in
the evidence document — and *then* the evidence document explains why it works,
commit by commit. That is a stronger sequence than the reverse.

## Running it

Tier 1 only: `reasoner.mode=llm` and `agent_runner.mode=real`. A stub reasoner
cannot decompose this brief, and running it at Tier 0 would prove nothing.

```bash
# 1. Materialize the target repository outside this monorepo.
./demos/static-site-v1/scripts/materialize.sh
export STATIC_SITE_REPO="$HOME/.orchestrator/demos/static-site-v1/repo"

# 2. Confirm the pin before spending anything. This reuses the fixture
#    preflight, which fails a mixed tier on purpose.
./fixtures/first-cycle-v1/scripts/preflight.sh

# 3. Create the project — repo_url MUST be the path from step 1. A project
#    without one gets a scratch repository and the run passes against a tree
#    you never looked at. This is the trap every walkthrough here documents.
API=./fixtures/first-cycle-v1/scripts/api.sh
$API POST /api/projects "$(jq -n --arg p "$STATIC_SITE_REPO" \
  '{name:"static-site-v1", repo_url:$p, binding:"local"}')"

# 4. Post the brief verbatim, then drive the two review gates (intent and
#    cycle draft) and let execution run. See fixtures/first-cycle-v1/README.md
#    for the exact gate calls — they are identical here.
$API POST /api/plans "$(jq -n --arg b "$(cat demos/static-site-v1/brief.txt)" \
  --arg proj "$PROJECT_ID" '{brief:$b, project_id:$proj}')"
```

## Checking it — twice, in the right order

**First, look at the product.** This is the check no evidence document can give
you and no container is needed for:

```bash
cd "$STATIC_SITE_REPO"
git switch "cycle/$CYCLE_ID"
PYTHONPATH=src python -m sitegen.cli build content/ --out /tmp/site
xdg-open /tmp/site/index.html    # or just open it
```

Then the automated acceptance check, held **outside** the repository so no agent
ever saw it:

```bash
SITEGEN_REPO="$STATIC_SITE_REPO" python -m pytest demos/static-site-v1/acceptance -q
```

It asserts what a reader would check by eye — a complete HTML document, the
front-matter title in `<title>`, headings at the right level, a real list,
`about.md` rewritten to `about.html`, no raw markdown leaking through, and HTML
escaping. `happy-path-v1` learned why this must live outside the repository:
its verdict was circular, because it ran pytest in the same `tests/` the agent
writes to.

**Second, check the orchestration.** Structural properties only, because a real
reasoner decomposes differently every run:

```bash
python demos/static-site-v1/scripts/verify_demo.py \
  --plan-id "$PLAN_ID" --cycle-id "$CYCLE_ID" --repo "$STATIC_SITE_REPO" \
  --seed-tag static-site-v1-seed
```

Exit `0` all checks passed · `1` a real finding · `2` the harness is broken and
nothing was checked. Those last two must never be conflated.

## Capturing the run

```bash
./fixtures/first-cycle-v1/scripts/capture-run.sh   # writes runs/<UTC>-…/
```

Record with it: reasoner provider and model, agent runtime and model,
orchestrator version (`orchestrate version`), wall-clock time, and cost. **A
result nobody can situate is an anecdote.**

## What this demo does not do

- **It does not exercise every capability.** Contract repair, block resolution,
  capacity backoff and planning recovery only exist on failure, and
  `contract-repair-v1` already has to poison a contract on purpose to reach
  one. A showcase that breaks on purpose is a bad showcase; adversarial
  coverage belongs in fixtures.
- **It is not a benchmark.** One run on one pin is not a sample. It shows what
  the system produces, not how often.
- **It is not locked in CI, ever.** See [`../README.md`](../README.md).
