# Happy-path v1 — notes

## Pins (change one variable per series)

Record what you used for the series:

| Pin | Example | Your value |
|---|---|---|
| Reasoner mode | `llm` / `stub` | |
| Reasoner provider/model | e.g. openrouter + model id | |
| Agent runner mode | `real` / `dry-run` | |
| Agent runtime_type | `pi` / `claude` / `gemini` | |
| `agent_runner.timeout_seconds` | 600 | |
| `max_concurrent_goals` | 1 recommended for Tier 1 | |
| Orchestrator git SHA | `git rev-parse --short HEAD` | |

Prefer **`max_concurrent_goals=1`** for this fixture so concurrency is not a confound.

## What this fixture covers

Backend API only — `curl` + `jq` against `:8000`, no Vite server and no browser.

- Project row + plan open + brief → intent gate  
- Cycle draft gate  
- JIT enrichment of one head goal  
- Real (or dry-run) task execution + verification command  
- Goal promotion + publication disposition  
- Reset/re-run comparability  

## What it deliberately does NOT cover

- Multi-goal DAG / dependency barriers (future Tier 2)  
- Conversational replan mid-flight  
- Full FastAPI/CRUD apps  
- Authenticated GitHub PR open  
- Bubblewrap sandbox  
- Multi-worker / multi-machine  
- **Anything frontend**: the UI is a second client of these same endpoints and is
  out of scope here — a step that cannot be done over the API is a finding  

Use separate labeled experiments for those.

## Known ambient flakes

- **Project row created without `repo_url`** — the worker then branches a freshly
  auto-seeded empty repo under `$ORCHESTRATOR_HOME/projects/<id>/repo` and the run
  "passes" against the wrong tree. Setting `PROJECT_REPO_DIR` does not fix this.  
- Free-tier provider rate limits (signal about capacity handling, not greeter difficulty)  
- Missing agent CLI binary (`GET /api/runner/status` before real runs)  
- Stale worktrees after hard kills — `reset.sh` + worker startup prune help  
- Unset `ORCHESTRATOR_MASTER_KEY` when real provider keys are required  

## Naming runs

```text
happy-path-v1-YYYYMMDD-a
happy-path-v1-YYYYMMDD-b
```

Same brief, same pins, new cycle or new plan after `reset.sh`.
