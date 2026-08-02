# first-cycle-v1 — one complete cycle, in one command

The fixture to run **first**. Every other fixture teaches the walkthrough a step
at a time, which is right for hunting bugs and wrong for a first run: an
operator who has never seen a review gate cannot tell "waiting for me" from
"stuck". This one drives the whole sequence — project, plan, discovery, intent
gate, cycle draft gate, execution, publication — and prints what it is waiting
for at every step.

**API only.** No Vite dev server, no browser, no frontend build. The UI is a
second client of these same endpoints; if a run needs it, that is itself a
finding.

| | |
|---|---|
| **Lives at** | `fixtures/first-cycle-v1/` |
| **Target repo** | Materialized *outside* this monorepo (default `~/.orchestrator/first-cycle-v1/repo`) |
| **Seed tag** | `first-cycle-v1-seed` |
| **Budget** | 1 goal, 1–2 tasks; 10–40 min on free models |
| **Tier** | Either. Tier 1 (real reasoner + real agent) is the point; Tier 0 completes free and deterministic |

## Run it

```bash
# 0. once per machine — the database must be at head, or /api/workers and the
#    evidence endpoint answer 500 for reasons that look like anything else
cd backend && set -a && . ./.env && set +a
python -m agent_orchestrator.infra.cli.main db upgrade

# 1. start the API and the worker (each needs the env exported)
python -m agent_orchestrator.infra.cli.main api start --port 8000 &
python -m agent_orchestrator.infra.cli.main worker start &

# 2. create the disposable target repository
cd .. && ./fixtures/first-cycle-v1/scripts/materialize.sh

# 3. drive one complete cycle
./fixtures/first-cycle-v1/scripts/run-cycle.sh
```

That is the whole walkthrough. The script exits non-zero with the server's own
explanation the moment the plan stops being able to progress, so a failure is a
finding you can paste, not a hang you have to interpret.

### Then check it actually did what it claimed

```bash
# the six critical-defect guards, against the live API
./fixtures/first-cycle-v1/scripts/guards.sh --plan "$PLAN_ID"

# the run's own success contract (see EXPECTATIONS.md)
python3 fixtures/first-cycle-v1/scripts/verify_run.py \
  --plan "$PLAN_ID" --repo ~/.orchestrator/first-cycle-v1/repo --tier 1
```

## Which tier am I in?

Tier is **data**, not an environment variable — it lives in the config store,
and `preflight.sh` prints and enforces it:

```bash
orchestrate config set reasoner.mode stub    ; orchestrate config set agent_runner.mode dry-run   # Tier 0
orchestrate config set reasoner.mode llm     ; orchestrate config set agent_runner.mode real      # Tier 1
```

Never mix the halves. A real reasoner verified by a dummy runner, or a dry-run
plan spending real tokens, produces evidence that means nothing —
`preflight.sh` fails a mixed pair on purpose.

Tier 1 also needs `ORCHESTRATOR_MASTER_KEY` (to decrypt the provider key) and
the CLI the agent is bound to (`pi`, `claude`). Free models make it affordable:
bind the agents to a `:free` model in the catalog and a full cycle costs
nothing but patience.

## What the scripts are

```text
fixtures/first-cycle-v1/
├── README.md          ← you are here
├── brief.txt          ← the locked brief, posted verbatim
├── EXPECTATIONS.md    ← the binary success contract
├── seed/              ← file templates for the target repo (not a git repo)
└── scripts/
    ├── api.sh         ← curl wrapper (base URL + bearer token, non-2xx exits 1)
    ├── materialize.sh ← create/reset the disposable repo, tag the seed
    ├── preflight.sh   ← everything that must be true before a cycle can start
    ├── run-cycle.sh   ← THE ONE COMMAND: project → … → publication
    ├── guards.sh      ← the critical defects from the Phase 4/5 review, live
    └── verify_run.py  ← expectations 1–7 from served facts + git
```

### `preflight.sh` — the checks that are not decorative

Each corresponds to a way a first run has actually been lost:

- **an unmigrated database** — the home DB sat at `0015` while the code expected
  `0017`, so worker liveness and the evidence endpoint were missing;
- **no worker** — leases prove a worker is *busy*; an idle one holds none, so
  before the first claim "running, nothing to do" and "never started" look
  identical over HTTP. `GET /api/workers` is the only read that separates them;
- **a broken agent binding** — fails the FIRST attempt, not the setup;
- **a missing CLI** — but only when an agent is bound to it. An install with
  three `pi` agents still reports `gemini` missing, and that must not block a
  run;
- **mixed tiers** — see above.

### `guards.sh` — the review findings, checked against a running server

The five defects found in the Phase 4/5 code review all have unit or integration
tests. Those run against a `TestClient` and a temporary database. These run
against *your* server and *your* state directory, so a green suite plus a red
guard means the deployment, not the code:

| Guard | The defect it re-checks |
|---|---|
| `max_inflight` 0/-1 refused, on providers and models | A value below 1 declined every attempt with nothing in flight — no circuit, no block, the plan waited forever |
| a valid capacity round-trips | The bound must not cost the feature it guards |
| `capacity_scope` typo refused | An unknown scope degrades to `per_model` at every read, silently |
| scp-style remote refused **by name** | `git@github.com:org/repo.git` was rejected as a missing local directory |
| captured attempt log readable | The resume offset was per batch, so a reconnect skipped lines |

Safe against a live install: one throwaway provider, created and deleted.

## Where the repository comes from

The worker resolves the checkout from the **project row's `repo_url`**, not from
an environment variable. `PROJECT_REPO_DIR` is read by nothing (a test enforces
that). A project created without `repo_url` gets a fresh empty repo under
`$ORCHESTRATOR_HOME/projects/<id>/repo` — and the run then "succeeds" against a
tree nobody looks at. `run-cycle.sh` always sets it.

## Re-running

`materialize.sh` resets the target repo to the seed tag, and every run creates a
new project and plan, so runs never collide. Old plans are disposable:

```bash
./fixtures/first-cycle-v1/scripts/api.sh DELETE "/api/plans/$OLD_PLAN_ID"
```

Deleting a plan cascades to its cycles, attempts, evidence, chat and telemetry,
and is refused with 409 `PLAN_BUSY` while a worker holds a live lease.
