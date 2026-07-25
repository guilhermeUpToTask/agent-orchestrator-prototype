# Happy-path v1 — re-runnable walkthrough fixture

A **small, locked** operator plan for proving the orchestrator end-to-end without
the noise of a full REST app. Use this when hunting backend bugs, not as a CI
test (pytest already covers dry-run cycles).

| | |
|---|---|
| **Lives at** | `fixtures/happy-path-v1/` (repo root — operator material, not product code) |
| **Target repo** | Materialized *outside* this monorepo (default `~/.orchestrator/happy-path-v1/repo`) |
| **Seed tag** | `happy-path-v1-seed` |
| **Budget** | 1 goal, 1–2 tasks, ~10–25 min real mode |

## Directory layout

```text
fixtures/happy-path-v1/
├── README.md                 ← you are here (where + how + agent workflow)
├── BRIEF.md                  ← paste this as the plan brief (locked text)
├── EXPECTATIONS.md           ← size budgets + binary success criteria
├── NOTES.md                  ← pins, known flakes, what this does NOT cover
├── seed/                     ← file templates (not a git repo)
│   ├── README.md
│   ├── pyproject.toml
│   ├── src/happy_path/...
│   └── tests/test_greeter.py
└── scripts/
    ├── materialize.sh        ← create/reset the disposable git repo
    ├── reset.sh              ← hard-reset target to seed tag
    └── check-success.sh      ← assert pytest green on a checkout
```

Why not under `backend/tests/`? Those are automated dual-backend truth tests.
This fixture is an **operator walkthrough**: real API, real worker, optional real
LLM/agent. Why not nested git inside the monorepo? Nested repos confuse worktrees
and accidental commits; materialize to `ORCHESTRATOR_HOME` instead.

---

## How to run (human)

### 0. Once per machine

```bash
# From repo root
backend/scripts/dev.sh doctor
backend/scripts/dev.sh setup
backend/scripts/dev.sh seed --stub          # free path
# OR real reasoner (needs key in env / backend/.env):
# backend/scripts/dev.sh seed --provider openrouter --model <model> --api-key-env OPENROUTER_API_KEY

# Materialize the target repo agents will edit
./fixtures/happy-path-v1/scripts/materialize.sh
```

Default target: `$ORCHESTRATOR_HOME/happy-path-v1/repo`  
(`ORCHESTRATOR_HOME` defaults to `~/.orchestrator`.)

Override:

```bash
HAPPY_PATH_REPO=/path/to/disposable/repo ./fixtures/happy-path-v1/scripts/materialize.sh
```

### 1. Point the orchestrator at that repo

```bash
export PROJECT_REPO_DIR="${HAPPY_PATH_REPO:-$HOME/.orchestrator/happy-path-v1/repo}"
# real agent runs also need:
#   config: agent_runner.mode=real
#   CLIs: pi / claude / gemini as bound on the agent
#   ORCHESTRATOR_MASTER_KEY if using encrypted provider keys
```

In the UI **Settings → Projects**, create/bind a project whose workspace is this
repo (or set `PROJECT_REPO_DIR` for the single global workspace path your
deployment uses). Prefer a named project bound to this path when the UI allows.

### 2. Start the stack

```bash
backend/scripts/dev.sh start --frontend
# API :8000  UI :5173  worker supervised together
```

### 3. Drive one cycle

1. Create/open the project plan.
2. Paste the entire contents of [`BRIEF.md`](BRIEF.md) as the brief.
3. Approve **intent** when the gate opens.
4. Inspect the **cycle draft**: if goals > 1 or tasks look like a platform rewrite, **edit/replan** — that is already a finding (see EXPECTATIONS).
5. Approve the draft.
6. Watch execution (Activity / agent events / attempt logs).
7. At publication: choose `retain_branch` (fine for re-runs).
8. Verify:

```bash
./fixtures/happy-path-v1/scripts/check-success.sh
# optional: snapshot for the paper trail
python backend/scripts/snapshot_current_plan.py --pretty -o /tmp/happy-path-run.json
```

### 4. Reset and re-run

```bash
# After the plan is idle / published (or abandoned)
./fixtures/happy-path-v1/scripts/reset.sh
# Start a new cycle (or new plan under the same project) with the SAME brief.
```

Do **not** keep stacking failed cycles on a dirty worktree. Reset is part of the method.

---

## Modes

| Mode | Config | Use when |
|---|---|---|
| **Tier 0 free** | `reasoner.mode=stub`, `agent_runner.mode=dry-run` | Lifecycle/UI/API wiring only |
| **Tier 1 real** | `reasoner.mode=llm`, `agent_runner.mode=real` | Daily backend walkthrough |

Tier 0 cannot prove verification/git promotion of real code. Tier 1 is the
happy path this fixture is for.

---

## Should you run this with an AI agent (Claude/Codex/Grok)?

**Yes — as the outer operator, not as the implementer.**

| Role | Who | Does |
|---|---|---|
| **Outer agent** (you + Claude/etc.) | Operator co-pilot | Drive gates via API/UI, watch worker logs, poll plan status, file bugs, snapshot state |
| **Inner agents** (pi/claude/gemini via worker) | Task runners | Edit the greeter repo only |
| **You** | Authority | Approve intent/draft when the outer agent is unsure; decide replan vs bug |

### Good outer-agent loop

```text
1. reset.sh + start stack
2. Create plan with BRIEF.md
3. Poll GET /api/plans/{id} (status, activity, legal_actions, block)
4. Approve gates when legal
5. On anomaly: capture plan id, attempt log, worker structlog, snapshot JSON
6. Write finding → docs/history/analyses/ or an issue
7. Do NOT implement greeter yourself mid-run
```

### What the outer agent must not do during a run

- Fix `greet()` in the target repo by hand (destroys the signal)
- Change orchestrator code mid-cycle unless the run is already a known failure
- Expand the brief into “also add FastAPI…” (use a different experiment)

### When *not* to use an outer agent

- First time learning the UI — drive it yourself once
- Pure Tier 0 dry-run smoke — overkill

Using Claude (or similar) the way you already were is the right way to catch
**realtime** bugs: rate-limit handling, pause lies, hot loops, bad legal_actions.
This fixture only makes those runs **comparable**.

---

## After N green runs

Graduation rule (from the design notes):

- **3 clean Tier 1 runs** (0 unexpected human fixes) → optional Tier 2 (two-goal dep; not in this folder yet)
- Any backend fix that touches execution/planning → re-prove **Tier 0 + one Tier 1** before calling it done

Findings from failed runs go to `docs/history/analyses/` (dated) or GitHub issues;
do not silently rewrite BRIEF.md mid-series. Bump to `happy-path-v2` if the
contract changes.
