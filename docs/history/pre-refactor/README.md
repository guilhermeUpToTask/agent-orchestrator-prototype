# AIPOM / Agent Orchestrator

A local Python system for **plan-driven software execution**. The center of the project is the planning layer: the `plan` workflow discovers requirements, proposes architecture, dispatches phase goals, and advances the project through review gates. Tasks, goals, workers, specs, and runtime adapters all exist to support that higher-level project plan. A React dashboard (in `frontend/`) visualizes the plan and streams execution progress live.

## Current status

The codebase recently went through a full-codebase review and a five-milestone remediation (see `docs/code-review-report.md` and the `review M1`–`M5` commits). The headline outcomes:

- **Reliable event delivery** — per-worker consumer groups (no more lost assignments), pending-message recovery on consumer restart.
- **Single coordinator process** — the FastAPI server hosts the task manager, goal orchestrator, and reconciler as lifespan threads; the goal orchestrator (including PR creation) runs out of the box.
- **Single-writer task state** — workers publish `task.execution_*` result events; only the task manager writes task state, making the file-backed CAS contract sound.
- **Real-time UI** — a Redis→SSE bridge fans worker/coordinator progress out to every connected dashboard tab; long planner operations (discovery, refinement) are `202 + session id` endpoints instead of multi-minute blocking requests.
- **Green build, enforced** — 1250+ tests passing, `ruff` clean, and `mypy src` at zero errors (`src/domain` and `src/app` fully strict; adapter layers carry documented relaxations in `pyproject.toml`).

Health gate for every change:

```bash
make check        # ruff + mypy + pytest
```

## What the project is for

The main objective of this codebase is **project-level orchestration**, not just task execution.

The current system combines:

- **Strategic planning as the top-level workflow**: `plan init`, `plan architect`, `plan review`, and `plan status` manage the lifecycle of a project plan.
- **Goal orchestration underneath the plan**: approved phases dispatch goals, and goals coordinate dependent task work, branch-level progress, and GitHub PRs.
- **Task execution underneath goals**: workers execute assigned tasks in isolated workspaces, while the task manager and reconciler keep execution moving.
- **Project spec governance**: `project_spec.yaml` constrains architecture and dependency choices, and spec changes are staged and operator-approved.

If you want to understand the product from the top down, start with the **`plan` command group** — or run the API + frontend and use the dashboard.

## Current architecture snapshot

```text
plan workflow (CLI) + dashboard (frontend/, via API + SSE)
  ↓
goals + project plan + spec governance
  ↓
task orchestration and worker execution
  ↓
runtimes, git workspaces, Redis/events, filesystem state
```

In code, that maps to:

```text
CLI (src/infra/cli)        API (src/api: FastAPI routers, SSE, sessions)
  ↓                          ↓
Application layer (src/app)
  - planner orchestration
  - goal orchestration
  - task handlers / use cases / coordinator runners
  ↓
Domain layer (src/domain)
  - project plan, goals, tasks, specs, ports, value objects
  ↓
Infrastructure layer (src/infra)
  - repositories, runtimes, Redis adapters, git, logging, GitHub
```

### Process topology

`orchestrate system start` boots exactly two kinds of processes:

```text
┌─ orchestrate system start ──────────────────────────────┐
│  uvicorn (FastAPI)                                      │
│   ├─ HTTP routers + per-client SSE fan-out              │
│   ├─ lifespan thread: Redis events:all → SSE bridge     │
│   ├─ lifespan thread: task manager loop                 │
│   ├─ lifespan thread: goal orchestrator (PRs enabled)   │
│   └─ lifespan thread: reconciler loop                   │
│                                                         │
│  worker processes (one per active agent)                │
│   └─ agent CLI subprocess + isolated git workspace      │
└─────────────────────────────────────────────────────────┘
```

- **Coordinators live in the API process** — one settings load, one writer process for task/goal state, direct access to the SSE bridge. Set `ORCHESTRATOR_EMBED_COORDINATORS=0` to opt out and run them standalone (`system task-manager`, `system reconciler`, `goals run`).
- **Workers stay separate processes** so a hung or resource-hungry agent session can be killed without taking the API down. The supervisor restarts crashed workers with exponential backoff and gives up after repeated fast crashes.
- **Workers never write task state.** They publish `task.execution_started/succeeded/failed` events with result facts; the task manager applies the transitions and emits the canonical `task.started/completed/failed` notifications.

Key design choices:

- **Plan-first workflow**: the project plan is the main control loop for the system.
- **Project-scoped state** lives under `~/.orchestrator/projects/<project_name>/...`.
- **Domain-first boundaries** keep business rules in `src/domain` and I/O in `src/infra` (enforced: no `app`/`infra`/`api` imports in domain, no `infra` imports in app).
- **Multiple runtime adapters** are supported per registered agent (`dry-run`, `claude`, `gemini`, `pi`).
- **PRs are opened by the orchestrator, merged by humans**: a goal reaching `READY_FOR_REVIEW` gets a PR against the base branch; the reconciler polls GitHub for checks/approval, and merging stays a human decision.
- **Execution observability** is built in through structured logging, persisted event journals, and the live SSE stream.

## CLI entry points

The canonical entry point after `pip install -e .` (or `uv pip install -e .`) is:

```bash
orchestrate --help
```

Without installing, the module form works identically:

```bash
python -m src.infra.cli.main --help
```

## CLI overview

### Top-level command groups

| Group | Commands | Purpose |
|---|---|---|
| `plan` | `init`, `architect`, `review`, `status`, `logs` | **Primary project workflow** |
| `goals` | `init`, `run`, `status`, `finalize` | Goal-level orchestration |
| `tasks` | `create`, `list`, `retry`, `delete`, `prune` | Low-level task management |
| `system` | `start`, `api`, `task-manager`, `worker`, `reconciler` | System processes |
| `agents` | `create`, `list`, `edit`, `delete` | Agent registry management |
| `spec` | `show`, `init`, `validate`, `propose`, `diff`, `apply` | Project-spec governance |
| `project` | `status`, `list`, `use`, `reset` | Active project management |
| `init` | `--defaults` | Setup wizard / default config |

### The workflow hierarchy

Think of the CLI in this order:

1. **`plan`** decides what the project should do next.
2. **`goals`** represent phase-approved chunks of work created or unlocked by the plan.
3. **`tasks`** are the execution units inside goals.
4. **`system`** runs the API (with embedded coordinators) and the workers.
5. **`agents`**, **`spec`**, and **`project`** support that lifecycle.

## API + frontend

The FastAPI server exposes the plan, goals, tasks, agents, and spec over REST, plus a real-time `GET /api/events` SSE stream. The React dashboard consumes it.

```bash
# Backend API (also hosts the coordinators)
orchestrate system api --port 8000

# Frontend dev server
cd frontend && npm install && npm run dev
```

Integration notes:

- **Live updates**: every domain event published to Redis (`events:all`) is bridged to SSE and fanned out per connected client; the canvas updates as workers execute. Each tab gets its own queue.
- **Long operations are sessions**: `POST /api/plan/refine` and `POST /api/plan/discovery/start` return `202 + session_id` immediately; progress streams over SSE and state/results are read from `GET /api/plan/sessions/{id}` / `GET /api/plan/discovery/{id}`.
- **Generated types**: the frontend's API types are generated from the OpenAPI schema — `cd frontend && npm run generate:api` after changing API schemas.

## Project layout on disk

At runtime the orchestrator derives project-specific paths from `ORCHESTRATOR_HOME` and the active project in `config.json`.

```text
~/.orchestrator/
  config.json
  projects/
    <project_name>/
      agents/registry.json
      events/
      goals/
      logs/
      planner_sessions/
      project.json
      project_plan.yaml
      project_spec.yaml
      project_state/
      repo/
      tasks/
      workspaces/
```

Important files:

- `~/.orchestrator/config.json` — machine config: active project, mode, Redis URL
- `project.json` — per-project operational settings such as source repo and GitHub settings (never secrets)
- `project_plan.yaml` — the persisted project plan and current phase state
- `planner_sessions/` — discovery / architecture / phase-review session records
- `project_spec.yaml` — architecture and dependency constraints used by validation and planning

## Plan-first quick start

This is the recommended way to understand and use the project.

### 1. Install dependencies

Use Python 3.11+.

```bash
uv pip install -e ".[dev]"     # or: pip install -e ".[dev]"
```

### 2. Initialize local config

```bash
orchestrate init --defaults
```

For interactive setup (dependency checks, agent registration, GitHub wiring) instead:

```bash
orchestrate init
```

### 3. Register at least one agent

A dry-run agent is the easiest starting point:

```bash
orchestrate agents create \
  --agent-id dry-run-001 \
  --name "Dry Run Worker" \
  --capabilities code:backend \
  --runtime-type dry-run
```

### 4. Start the plan workflow

Begin with discovery:

```bash
orchestrate plan init --dry-run
```

That stage gathers requirements, produces a project brief, and asks for operator approval.

After approving the brief, move into architecture planning:

```bash
orchestrate plan architect --dry-run
```

That stage proposes decisions and phases. Decisions can be approved, rejected, or edited in `$EDITOR` before approval. When approved, it dispatches the first phase's goals.

### 5. Inspect project-plan state

```bash
orchestrate plan status
```

Use this command to understand where the project is in the lifecycle before reaching for lower-level `goals` or `tasks` commands.

## Planning workflow

The **main point of the project** is this planning loop. All three session commands support `--dry-run`, and `plan logs` replays the persisted session log of any run.

### 1. Discovery — `plan init`

- starts or resumes a **discovery** session
- uses the interactive planner runtime to gather project requirements
- prints a generated **project brief**
- asks the operator whether to approve the brief

If approved, the plan moves from `discovery` to `architecture`.

### 2. Architecture — `plan architect`

Runs when the plan is in `architecture` state:

- runs the architecture planning session
- shows pending architectural decisions and proposed phases
- asks the operator which decisions to approve (`y`/`n`/`edit`)
- asks whether to approve the phase plan and start execution

When approved, the orchestrator applies approved decisions and any derived spec changes, transitions the plan into `phase_active`, and dispatches goals for the first approved phase.

### 3. Phase review — `plan review`

Runs when the plan is in `phase_review` state:

- runs the review session for the completed phase
- prints lessons learned and the next phase proposal
- surfaces any pending decisions
- asks whether to continue with the next phase, or — if not — whether to mark the project done (declining both leaves the plan unchanged)

### 4. Inspecting the plan — `plan status` / `plan logs`

```bash
orchestrate plan status
orchestrate plan logs --filter tools --tail 20
```

## Supporting workflows under the plan

### Goals

Goals are the layer directly below the plan. Approved phases dispatch or unlock goals; the goal orchestrator (embedded in the API) merges completed task branches into the goal branch, triggers JIT task planning when goals unblock, and opens a PR when a goal reaches `READY_FOR_REVIEW`. **The orchestrator never merges PRs** — that stays with humans (or a merge queue).

```bash
orchestrate goals init <goal-file.yaml>
orchestrate goals status
orchestrate goals finalize <goal_id>
orchestrate goals run        # standalone orchestrator loop (escape hatch;
                             # normally it runs inside the API process)
```

### Tasks

Tasks are the execution units below goals — useful for operators and debugging, but not the main project-level entry point.

```bash
orchestrate tasks create \
  --title "Add health endpoint" \
  --description "Implement a basic health endpoint and tests" \
  --capability code:backend \
  --allow src/api/health.py \
  --allow tests/test_health.py \
  --test "pytest tests/test_health.py"

orchestrate tasks list
orchestrate tasks retry <task_id>
```

## Operating modes

### Dry-run mode

Default mode is `dry-run`. Use it to exercise the planning workflow locally, run the full system without Redis or live agent CLIs, and develop planner/domain behavior with minimal external dependencies. `AGENT_MODE=dry-run orchestrate system start` boots the whole topology with in-memory adapters and simulated agents.

### Real mode

Set `AGENT_MODE=real` to use Redis-backed events and live runtime adapters.

Boot everything from the active registry:

```bash
AGENT_MODE=real orchestrate system start
```

`system start` boots the API (which hosts the task manager, goal orchestrator, reconciler, and SSE bridge), launches one worker per active agent, waits for heartbeats, then supervises the workers — restarting crashes with backoff and shutting the system down if the API exits.

Standalone processes remain available as escape hatches (pair them with `ORCHESTRATOR_EMBED_COORDINATORS=0` on the API so task/goal state keeps a single writer process):

```bash
AGENT_MODE=real orchestrate system task-manager
AGENT_MODE=real orchestrate system worker --agent-id dry-run-001
AGENT_MODE=real orchestrate system reconciler
```

## Agent runtimes

The runtime factory currently supports these runtime types:

- `dry-run`
- `gemini`
- `claude`
- `pi`

Runtime-specific options are stored on each agent record in `runtime_config`, which allows multiple differently configured agents to coexist in the same registry.

## Project spec workflow

The project spec is the canonical source of architectural constraints used by planning and validation. Agents never write the spec directly — the only mutation flow is:

```text
spec propose → spec diff → spec apply
```

Representative commands:

```bash
orchestrate spec show
orchestrate spec init
orchestrate spec validate --description "add redis cache"
orchestrate spec propose --add-required fastapi
orchestrate spec diff
orchestrate spec apply
```

Spec-derived behavior (validation, planner context, PR CI gates) reloads the spec per use, so `spec apply` takes effect on a running API without a restart.

## Configuration reference

Primary environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODE` | `dry-run` | Selects dry-run vs Redis/live runtime behavior (CLI `--dry-run` flags override per command) |
| `AGENT_ID` | unset | Worker identity for `system worker` (or pass `--agent-id`) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection for real mode |
| `TASK_TIMEOUT_SECONDS` | `600` | Per-task runtime timeout |
| `ORCHESTRATOR_HOME` | `~/.orchestrator` | Root orchestrator state directory |
| `RECONCILER_INTERVAL` | `60` | Embedded reconciler poll interval (seconds) |
| `RECONCILER_STUCK_AGE` | `120` | Stuck-task threshold (seconds) |
| `ORCHESTRATOR_EMBED_COORDINATORS` | `1` | Set `0` to keep coordinator threads out of the API process |
| `ANTHROPIC_API_KEY` | empty | Claude / pi-anthropic runtime auth |
| `GEMINI_API_KEY` | empty | Gemini / pi-gemini runtime auth |
| `OPENROUTER_API_KEY` | empty | pi-openrouter runtime auth |
| `GITHUB_TOKEN` | empty | GitHub API auth for PR creation/polling |

The active project is **not** an environment variable — it lives in `~/.orchestrator/config.json` and is switched with `orchestrate project use <name>`.

## Testing & quality gates

```bash
make check                  # ruff + mypy + pytest — the gate for every change
pytest tests/unit           # domain invariants and use-case logic
pytest tests/integration    # fakeredis-backed pipelines, API, lifespan, e2e
```

Conventions:

- Unit tests mock repositories/ports (`MagicMock`, `InMemoryEventAdapter`, `StubGitHubClient`).
- Integration tests use `fakeredis` (≥ 2.36 — earlier versions lose messages on multi-stream reads) and `tmp_path` for all file I/O.
- Typing is a ratchet: `mypy src` must stay at zero errors; `src/domain` and `src/app` are fully strict, and the per-module relaxations for `src/infra`/`src/api` in `pyproject.toml` should only ever shrink.

## Additional documentation

- `docs/architecture.md` — architecture, runtime workflows, and boundaries
- `docs/code-review-report.md` — the full codebase review that drove the M1–M5 remediation
- `CLAUDE.md` — architectural invariants and contribution rules
- `roadmap.md` — current-state roadmap and likely next milestones
- `src/infra/logging/README.md` — runtime logging subsystem details
