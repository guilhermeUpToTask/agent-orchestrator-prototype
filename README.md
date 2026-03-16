# Agent Orchestrator

A local Python prototype implementing a multi-agent orchestration system using **hexagonal architecture** and an **event-driven core** (Redis Streams). Agents are stateless CLI runtimes supervised per-task — pi, Claude Code, or Gemini CLI.

## Architecture

```
Interface Layer  (CLI — src/cli.py)
       ↑
Application Layer  (TaskManagerHandler, WorkerHandler, Reconciler)
       ↑
Ports / Adapters   (TaskRepo, LeasePort, EventPort, GitWorkspace, AgentRuntime)
       ↑
Domain Layer       (TaskAggregate, AgentProps, SchedulerService)
```

**Core principles:**
- **SSoT:** `workflow/` is canonical state. Never bypass it.
- **Persist-first:** state is written + fsynced before any event is emitted.
- **Stateless sessions:** fresh agent CLI session per task, no reuse.
- **Workspace isolation:** ephemeral git clone per task on branch `task/<task_id>`.
- **Swappable runtimes:** pi / claude / gemini declared per-agent in `registry.json`.

---

## Getting started

### Requirements

- [VS Code](https://code.visualstudio.com/) with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
- Docker Desktop (or compatible runtime)

That's it. Redis, Node.js, Python, and the agent CLIs are all inside the container.

### Open in devcontainer

```
Cmd/Ctrl + Shift + P → Dev Containers: Reopen in Container
```

On first open VS Code builds the image (~2 min) and installs Python deps.
Redis starts automatically in the background — `redis://localhost:6379/0`.

### Configure API keys

Copy `.env.example` to `.env` and fill in the keys you need:

```bash
cp .env.example .env
# edit .env — add ANTHROPIC_API_KEY and/or GEMINI_API_KEY
```

---

## Running

### Dry-run (no API key, no real agent)

```bash
# All unit + integration tests — no Redis, no API key
pytest -m "not e2e and not redis"

# Demo script — full lifecycle with simulated agent
AGENT_MODE=dry-run python scripts/demo.py
```

### With real Redis (inside devcontainer — Redis is already running)

```bash
# Tests that exercise real Redis Streams + leases
pytest -m redis -v
```

### With a real agent CLI (e2e)

```bash
# Full end-to-end: real repo, real pi/claude agent, real code produced
RUN_E2E=true pytest tests/e2e/ -v -s -m e2e
```

---

## Registering agents

Agents are declared in `workflow/agents/registry.json`. Three runtimes are supported:

```bash
# pi (Anthropic backend — default)
python -m src.cli register-agent \
  --agent-id pi-worker-001 \
  --name "Pi Worker" \
  --capabilities "code:backend" \
  --runtime-type pi \
  --runtime-config '{"backend":"anthropic","model":"claude-sonnet-4-5"}'

# pi (Gemini backend)
python -m src.cli register-agent \
  --agent-id pi-gemini-001 \
  --name "Pi Gemini Worker" \
  --capabilities "code:backend" \
  --runtime-type pi \
  --runtime-config '{"backend":"gemini","model":"gemini-2.0-flash"}'

# pi (OpenRouter)
python -m src.cli register-agent \
  --agent-id pi-or-001 \
  --name "Pi OpenRouter Worker" \
  --capabilities "code:backend" \
  --runtime-type pi \
  --runtime-config '{"backend":"openrouter","model":"anthropic/claude-sonnet-4-5"}'

# Claude Code
python -m src.cli register-agent \
  --agent-id claude-worker-001 \
  --name "Claude Worker" \
  --capabilities "code:backend" \
  --runtime-type claude
```

---

## Running the system (real mode)

Three processes, three terminals:

```bash
# Terminal 1 — Task Manager (assigns tasks to agents)
AGENT_MODE=real python -m src.cli task-manager

# Terminal 2 — Worker (executes tasks via agent CLI)
AGENT_MODE=real AGENT_ID=pi-worker-001 python -m src.cli worker

# Terminal 3 — Reconciler (detects stuck/expired tasks)
AGENT_MODE=real python -m src.cli reconciler
```

Or boot all at once from the registry:

```bash
AGENT_MODE=real python -m src.cli start
```

Create a task:

```bash
AGENT_MODE=real python -m src.cli create-task \
  --title "Add /healthz endpoint" \
  --description "Return 200 OK with {status: ok}" \
  --capability "code:backend" \
  --files "src/api/health.py"

# Watch status
AGENT_MODE=real python -m src.cli list-tasks
```

---

## Project structure

```
.devcontainer/
  Dockerfile          # Dev container image (Redis + Node + Python + agent CLIs)
  devcontainer.json   # VS Code devcontainer config

src/
  core/
    models.py         # Domain models (TaskAggregate, AgentProps, ...)
    ports.py          # Abstract port interfaces
    services.py       # Pure domain services
  app/
    handlers/
      task_manager.py # Assign tasks to agents
      worker.py       # Execute tasks via agent CLI
    reconciler.py     # Recover stuck/expired tasks
  infra/
    config.py         # OrchestratorConfig (pydantic-settings, SecretStr for keys)
    factory.py        # DI wiring — builds adapters from config
    fs/               # YAML task repo + JSON agent registry
    redis_adapters/   # Redis Streams event/lease adapters + in-memory stubs
    git/              # Git workspace adapter + dry-run stub
    runtime/          # agent_runtime base, gemini, claude, pi, dry-run
  cli.py              # Click CLI entry points

workflow/             # Canonical SSoT (committed to git)
  agents/registry.json
  tasks/

tests/
  unit/               # Fast, no external deps
  integration/        # Full lifecycle, in-memory adapters
  e2e/                # Real agent CLI — opt-in with RUN_E2E=true
```

---

## Environment variables

All config lives in `.env` (loaded by `OrchestratorConfig` via pydantic-settings).

| Variable | Default | Description |
|---|---|---|
| `AGENT_MODE` | `dry-run` | `dry-run` (stubs) or `real` (Redis + subprocess) |
| `AGENT_ID` | `agent-worker-001` | Identity of this worker process |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `ANTHROPIC_API_KEY` | — | For pi (anthropic), claude runtimes |
| `GEMINI_API_KEY` | — | For pi (gemini), gemini runtimes |
| `OPENROUTER_API_KEY` | — | For pi (openrouter) runtime |
| `ORCHESTRATOR_HOME` | `~/.orchestrator` | Root dir for tasks, registry, workspaces |
| `TASK_TIMEOUT_SECONDS` | `600` | Max agent session runtime |

---

## Task state machine

```
created → assigned → in_progress → succeeded → merged
                                 ↘ failed → requeued → (assigned again)
                                          ↘ canceled (max retries)
```

Every transition: writes YAML + fsyncs → increments `state_version` → emits Redis Stream event.

---

## Test layers

```bash
pytest -m "not e2e and not redis"   # unit + integration — no deps, fast
pytest -m redis                     # real Redis Streams — devcontainer
RUN_E2E=true pytest -m e2e          # real agent — needs API key + CLI
```