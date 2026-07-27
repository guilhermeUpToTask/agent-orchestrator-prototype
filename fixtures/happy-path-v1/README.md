# Happy-path v1 — re-runnable walkthrough fixture

A **small, locked** operator plan for proving the orchestrator end-to-end without
the noise of a full REST app. Use this when hunting backend bugs, not as a CI
test (pytest already covers dry-run cycles).

**This fixture is backend-only.** Every step below is an HTTP call against the
API — no Vite dev server, no browser, no frontend build. The UI is a second
client of the same endpoints; if a run needs it, that is itself a finding.

| | |
|---|---|
| **Lives at** | `fixtures/happy-path-v1/` (repo root — operator material, not product code) |
| **Target repo** | Materialized *outside* this monorepo (default `~/.orchestrator/happy-path-v1/repo`) |
| **Seed tag** | `happy-path-v1-seed` |
| **Budget** | 1 goal, 1–2 tasks, ~10–25 min real mode |
| **Interface** | `GET/POST /api/…` via `curl` + `jq` only |

## Directory layout

```text
fixtures/happy-path-v1/
├── README.md                 ← you are here (where + how + agent workflow)
├── brief.txt                 ← the locked brief, posted verbatim as the plan brief
├── BRIEF.md                  ← how to post it + the text for humans
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
    ├── api.sh                ← curl wrapper (base URL + bearer token)
    ├── check-success.sh      ← assert pytest green on a checkout (expectation 7)
    ├── verify_run.py         ← assert expectations 1-6 + 8 from plan and git facts
    └── capture-run.sh        ← collect one run into a named evidence directory
```

Why not under `backend/tests/`? Those are automated dual-backend truth tests.
This fixture is an **operator walkthrough**: real API, real worker, optional real
LLM/agent. Why not nested git inside the monorepo? Nested repos confuse worktrees
and accidental commits; materialize to `ORCHESTRATOR_HOME` instead.

---

## How to run

Requires `curl` and `jq`. All commands run from the repo root.

### 0. Once per machine

```bash
backend/scripts/dev.sh doctor
backend/scripts/dev.sh setup --backend-only
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

### 1. Start the backend

```bash
export HAPPY_PATH_REPO="${HAPPY_PATH_REPO:-$HOME/.orchestrator/happy-path-v1/repo}"
# real agent runs also need:
#   config: agent_runner.mode=real
#   CLIs: pi / claude / gemini as bound on the agent
#   ORCHESTRATOR_MASTER_KEY if using encrypted provider keys

backend/scripts/dev.sh start          # API :8000 + worker; no --frontend
```

⚠️ **The repo path comes from the project row, not from `PROJECT_REPO_DIR`.**
For a project-bound plan the worker resolves the checkout from the project's
`repo_url` (`ProjectWorkspaceResolver._repository_path`): a bare local path or a
`file://` URL is used in place, and a project with **no** `repo_url` gets a fresh
empty repo auto-seeded at `$ORCHESTRATOR_HOME/projects/<project_id>/repo`. Create
the project with `repo_url` set (step 3) or the run will silently succeed against
the wrong tree. `PROJECT_REPO_DIR` only names the legacy single global workspace
and does not apply here.

### 2. Set up the API session

```bash
export HAPPY_PATH_API="${HAPPY_PATH_API:-http://127.0.0.1:8000}"
# export ORCHESTRATOR_API_TOKEN=…   # only if the API was started with a token
api() { ./fixtures/happy-path-v1/scripts/api.sh "$@"; }

# before any real run: mode, binding validity, and CLI binary probes
api GET /api/runner/status | jq '{mode, valid, detail,
  binaries: [.binaries[] | {name, ok, message}],
  agents: [.agents[] | {agent_name, runtime_type, valid, detail}]}'
```

`api.sh` adds the base URL and `Authorization: Bearer $ORCHESTRATOR_API_TOKEN`
(omitted when unset), prints the body, and exits non-zero on any non-2xx.

### 3. Create the project and open the plan with the locked brief

```bash
# repo_url is REQUIRED: it is what the worker branches in
PROJECT_ID="$(api POST /api/projects \
  "{\"name\":\"happy-path-v1\",\"repo_url\":\"$HAPPY_PATH_REPO\"}" | jq -r .id)"

BRIEF="$(jq -Rs . < fixtures/happy-path-v1/brief.txt)"
PLAN_ID="$(api POST /api/plans "{\"brief\":$BRIEF,\"project_id\":\"$PROJECT_ID\"}" \
  | jq -r .plan_id)"
echo "plan=$PLAN_ID project=$PROJECT_ID"
```

`POST /api/plans` runs the first discovery turn inline; its response carries
`discovery_status` and `discovery_reply`. Reuse an existing project row on
re-runs — only the plan/cycle is disposable.

**The brief may not open the intent gate on its own.** Discovery is multi-turn:
the real reasoner usually commits on the first turn (`discovery_status:
"committed"`), while the stub returns `waiting_for_user`
(`legal_actions: ["start_intent"]`). Poll `pending_gate`, and keep replying until
a turn comes back `committed: true`:

```bash
api POST "/api/plans/$PLAN_ID/discovery/message" \
  '{"message":"Yes, that is right. Proceed."}' | jq '{committed, reply}'
```

### 4. Poll, then approve each gate

One poll command for the whole run:

```bash
watch_plan() {
  api GET "/api/plans/$PLAN_ID" | jq '{
    status, status_reason, activity, legal_actions,
    planning_progress,
    gate: (.pending_gate | if . == null then null else {id, subject_type, subject_revision, allowed_decisions} end),
    block: (.block // (.goal_blocks | if . == {} then null else . end)),
    waiting: .provider_waiting
  }'
}
watch_plan
```

A gate is approved by echoing back its exact id and revision — a stale revision
is rejected on purpose:

```bash
gate_body() { api GET "/api/plans/$PLAN_ID" \
  | jq -c '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision}'; }

# 4a. intent gate  (pending_gate.subject_type == "intent")
api POST "/api/plans/$PLAN_ID/intent/approve" "$(gate_body)"

# 4b. inspect the cycle draft BEFORE approving
api GET "/api/plans/$PLAN_ID" \
  | jq '.cycle_draft.goals | {count: length, goals: [.[] | {key, name, position, depends_on}]}'
```

If goals > 1 or the tasks look like a platform rewrite, **do not push through** —
that is already a finding (see [EXPECTATIONS.md](EXPECTATIONS.md)). Revise the
draft (`PUT /api/plans/$PLAN_ID/cycle-draft`) or cancel it
(`DELETE …/cycle-draft`) and reshape the intent by conversation.

```bash
# 4c. draft gate  (pending_gate.subject_type == "cycle_draft") → activates the cycle
api POST "/api/plans/$PLAN_ID/cycle-draft/approve" "$(gate_body)" | jq '{id, goals: (.goals | length)}'
```

### 5. Watch execution

```bash
# domain event feed (named SSE events; Ctrl-C to stop)
curl -N ${ORCHESTRATOR_API_TOKEN:+-H "Authorization: Bearer $ORCHESTRATOR_API_TOKEN"} \
  "$HAPPY_PATH_API/api/events"

# attempt timeline + one attempt's log
api GET "/api/plans/$PLAN_ID/attempts" \
  | jq '.tasks[] | {task_id, runs: [.runs[] | {status, attempts: [.attempts[] | {id, number, status, failure_kind, retryable}]}]}'
api GET "/api/plans/$PLAN_ID/attempts/<ATTEMPT_ID>/log" | jq -r '.entries[] | "\(.stream)\t\(.text)"'

# fine-grained agent telemetry
api GET "/api/plans/$PLAN_ID/agent-events" | jq '.[-20:]'
```

### 6. Publication and verification

```bash
# publication gate  (pending_gate.subject_type == "cycle_completion")
# `output_reference` is MANDATORY for every non-discard disposition:
# without it the gate returns 422 INVALID_EDIT.
CYCLE_ID="$(api GET "/api/plans/$PLAN_ID" | jq -r .active_cycle.id)"
api POST "/api/plans/$PLAN_ID/publication" \
  "$(gate_body | jq -c --arg ref "cycle/$CYCLE_ID" \
       '. + {disposition:"retain_branch", output_reference:$ref}')"

# verify the run against the binary success contract (EXPECTATIONS.md 1-6 + 8):
# cycle activated, size budget, tasks DONE with accepted revision-bound evidence,
# goals promoted, no open block, disposition recorded, root idle — plus the git
# chain (cycle branch descends from the seed, goal branches merged into it, the
# default branch untouched, the repo isolated from the orchestrator checkout).
./fixtures/happy-path-v1/scripts/verify_run.py --plan-id "$PLAN_ID"

# expectation 7 (Tier 1 only) — pytest green on a checkout of the cycle branch:
./fixtures/happy-path-v1/scripts/verify_run.py --plan-id "$PLAN_ID" --tier 1
```

**Capture every run, green or red**, into one named evidence directory —
manifest (fixture version, orchestrator SHA, pinned runtime, check summary),
plan snapshot, evidence bundle, attempt timeline, telemetry, and the worker-log
reference. Two runs are only comparable if you can say what produced each:

```bash
export HAPPY_PATH_WORKER_LOG=…            # optional; copied into the run dir
./fixtures/happy-path-v1/scripts/capture-run.sh "$PLAN_ID"      # Tier 0
./fixtures/happy-path-v1/scripts/capture-run.sh "$PLAN_ID" 1    # Tier 1
# → $ORCHESTRATOR_HOME/happy-path-v1/runs/<UTC>-tier<N>-<plan prefix>/
```

`capture-run.sh` exits 0 only when the run is green, 1 when a check failed (the
capture still completes — a red run's evidence is the point), 2 when collection
itself broke.

`retain_branch` is the right disposition for re-runs. Recording it returns the
root to IDLE (`status: "idle"`, `legal_actions: ["start_intent"]`).

**In Tier 0 `check-success.sh` exits 1 — that is correct, not a regression.** The
dry-run runner promotes branches without writing an implementation, so `greet()`
still raises `NotImplementedError` on the cycle branch. Tier 0 proves the
lifecycle and the git promotion chain; only Tier 1 can turn that check green.

### 7. Reset and re-run

```bash
# After the plan is idle / published (or abandoned)
./fixtures/happy-path-v1/scripts/reset.sh
# Start a new cycle (or a new plan under the SAME project) with the SAME brief.
```

Do **not** keep stacking failed cycles on a dirty worktree. Reset is part of the method.

### Interventions, when a run goes sideways

```bash
# graceful pause: the worker stops claiming, an active run finalizes first
api POST "/api/plans/$PLAN_ID/pause" '{"reason":"operator inspection"}'
# removes a manual pause only — never retries, clears backoff, or resolves a block
api POST "/api/plans/$PLAN_ID/resume"
# one named failed task (both ids required)
api POST "/api/plans/$PLAN_ID/retry" '{"goal_id":"<GOAL_ID>","task_id":"<TASK_ID>"}'
# a blocked planning stage (enrichment/architecture); goal_id only when ambiguous
api POST "/api/plans/$PLAN_ID/retry-stage" '{"goal_id":"<GOAL_ID>"}'
```

Only issue what `legal_actions` currently lists. A `POST` that the plan's own
`legal_actions` advertised but that returns 4xx is a domain/API mismatch — file it.

---

## Modes

| Mode | Config | Use when |
|---|---|---|
| **Tier 0 free** | `reasoner.mode=stub`, `agent_runner.mode=dry-run` | Lifecycle/API wiring only |
| **Tier 1 real** | `reasoner.mode=llm`, `agent_runner.mode=real` | Daily backend walkthrough |

🚫 **The two keys move together. Never run one real and the other faked.** A real
reasoner freezes a real contract — exact path scope, a real `pytest` command, a
TDD RED stage — and the dry-run runner satisfies none of it: it writes a marker
file and no code, so the goal blocks on a candidate that was never going to pass.
That configuration produces failures that belong to neither mode and tempt you
into loosening the verification boundary to make a fake runner look green. Flip
both keys, or neither.

Read/flip config over the API too:

```bash
api GET /api/config/orchestrator | jq
api PUT /api/config/orchestrator/agent_runner.mode '{"value":"real"}'
```

Tier 0 cannot prove verification/git promotion of real code. Tier 1 is the
happy path this fixture is for.

---

## Should you run this with an AI agent (Claude/Codex/Grok)?

**Yes — as the outer operator, not as the implementer.**

| Role | Who | Does |
|---|---|---|
| **Outer agent** (you + Claude/etc.) | Operator co-pilot | Drive gates via the API, watch worker logs, poll plan status, file bugs, snapshot state |
| **Inner agents** (pi/claude/gemini via worker) | Task runners | Edit the greeter repo only |
| **You** | Authority | Approve intent/draft when the outer agent is unsure; decide replan vs bug |

An API-only fixture is what makes this delegable: every step is a scriptable
call with a checkable exit code, and nothing depends on reading a screen.

### Good outer-agent loop

```text
1. reset.sh + start backend (no --frontend)
2. Create project + plan with BRIEF.md
3. Poll GET /api/plans/{id} (status, activity, legal_actions, pending_gate, block)
4. Approve gates when legal, echoing gate_id + subject_revision
5. On anomaly: capture plan id, attempt log, worker structlog, snapshot JSON
6. Write finding → docs/history/analyses/ or an issue
7. Do NOT implement greeter yourself mid-run
```

### What the outer agent must not do during a run

- Fix `greet()` in the target repo by hand (destroys the signal)
- Change orchestrator code mid-cycle unless the run is already a known failure
- Expand the brief into "also add FastAPI…" (use a different experiment)
- Mutate SQLite directly instead of going through the API (the API surface *is*
  what this fixture tests)


## After N green runs

Graduation rule (from the design notes):

- **3 clean Tier 1 runs** (0 unexpected human fixes) → optional Tier 2 (two-goal dep; not in this folder yet)
- Any backend fix that touches execution/planning → re-prove **Tier 0 + one Tier 1** before calling it done

Findings from failed runs go to `docs/history/analyses/` (dated) or GitHub issues;
do not silently rewrite BRIEF.md mid-series. Bump to `happy-path-v2` if the
contract changes.
