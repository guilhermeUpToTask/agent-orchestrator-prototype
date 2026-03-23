# Agent Orchestrator Prototype

A local Python prototype for coordinating CLI-based coding agents across tasks, goals, and higher-level project planning. The system combines a hexagonal core, project-scoped filesystem state, Redis-backed event orchestration, isolated git workspaces, and optional GitHub PR integration.

## What the project does today

The current codebase supports four connected layers of work:

- **Task orchestration**: create tasks, assign them to registered agents, execute them in isolated workspaces, retry failures, and reconcile stuck work.
- **Goal orchestration**: group related tasks under a goal, merge completed task branches into a goal branch, and finalize the goal when all work is complete.
- **Project specification enforcement**: maintain a canonical `project_spec.yaml`, validate work against architectural constraints, and stage spec changes through an approval flow.
- **Strategic planning**: run a multi-phase planning workflow (`plan init`, `plan architect`, `plan review`) that persists a project brief, phases, decisions, and plan state.

## Current architecture snapshot

```text
CLI (src/infra/cli)
  ↓
Application layer (src/app)
  - use cases
  - task manager / worker handlers
  - goal orchestrator
  - planning orchestration
  ↓
Domain layer (src/domain)
  - aggregates, entities, value objects
  - repository ports
  - domain services and rules
  ↓
Infrastructure layer (src/infra)
  - filesystem repositories
  - Redis adapters
  - git workspace adapters
  - agent runtimes
  - GitHub client
  - logging / observability
```

Key design choices:

- **Project-scoped state** lives under `~/.orchestrator/projects/<project_name>/...`.
- **Domain-first boundaries** keep business rules in `src/domain` and I/O in `src/infra`.
- **Multiple runtime adapters** are supported per registered agent (`dry-run`, `claude`, `gemini`, `pi`).
- **Execution observability** is built in through runtime logging and persisted event journals.
- **Planning and execution are connected** through project plans, goals, tasks, and spec-aware validation.

## CLI entry points

The canonical CLI entry point is:

```bash
python -m src.infra.cli.main --help
```

A compatibility shim still exists, so this also works:

```bash
python -m src.cli --help
```

Top-level command groups:

- `init` — create `.orchestrator/config.json`
- `agents` — register, edit, list, and delete agents
- `tasks` — create, list, retry, delete, and prune tasks
- `system` — run the task manager, workers, reconciler, or boot them together
- `goals` — initialize and run goal-driven execution
- `spec` — inspect, validate, propose, diff, and apply project spec changes
- `plan` — run discovery, architecture, and review planning phases
- `project` — reset the active project state

## Project layout on disk

At runtime the orchestrator derives project-specific paths from `ORCHESTRATOR_HOME` and `PROJECT_NAME`.

```text
~/.orchestrator/
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

- `.orchestrator/config.json` — local CLI config for the current working tree
- `project.json` — per-project operational settings such as source repo and GitHub settings
- `project_spec.yaml` — architectural and dependency constraints
- `project_plan.yaml` — strategic plan state and phases

## Quick start

### 1. Install dependencies

Use Python 3.11+.

```bash
pip install -e .
```

### 2. Initialize local config

```bash
python -m src.infra.cli.main init --defaults
```

For interactive setup instead:

```bash
python -m src.infra.cli.main init
```

### 3. Register an agent

A dry-run agent is the simplest way to exercise the workflow without external API access:

```bash
python -m src.infra.cli.main agents create \
  --agent-id dry-run-001 \
  --name "Dry Run Worker" \
  --capabilities code:backend \
  --runtime-type dry-run
```

### 4. Create a task

```bash
python -m src.infra.cli.main tasks create \
  --title "Add health endpoint" \
  --description "Implement a basic health endpoint and tests" \
  --capability code:backend \
  --allow src/api/health.py \
  --allow tests/test_health.py \
  --test "pytest tests/test_health.py"
```

### 5. Inspect the queue

```bash
python -m src.infra.cli.main tasks list
python -m src.infra.cli.main agents list
```

## Operating modes

### Dry-run mode

Default mode is `dry-run`.

Use it when you want to:

- exercise the CLI and orchestration flow locally
- run tests without Redis or live agent CLIs
- develop planner and domain behavior with minimal external dependencies

### Real mode

Set `AGENT_MODE=real` to use Redis-backed events and live runtime adapters.

Typical long-running processes:

```bash
AGENT_MODE=real python -m src.infra.cli.main system task-manager
AGENT_MODE=real AGENT_ID=dry-run-001 python -m src.infra.cli.main system worker
AGENT_MODE=real python -m src.infra.cli.main system reconciler
```

Or let the CLI boot all registered active workers plus supporting daemons:

```bash
AGENT_MODE=real python -m src.infra.cli.main system start
```

## Agent runtimes

The runtime factory currently supports these runtime types:

- `dry-run`
- `gemini`
- `claude`
- `pi`

Runtime-specific options are stored on each agent record in `runtime_config`, which allows multiple differently configured agents to coexist in the same registry.

## Goals, planning, and spec management

### Goals

Goals coordinate a group of dependent tasks and track progress at the branch level.

Core goal commands:

```bash
python -m src.infra.cli.main goals init <goal-file.yaml>
python -m src.infra.cli.main goals status
python -m src.infra.cli.main goals run
python -m src.infra.cli.main goals finalize <goal_id>
```

### Project spec

The project spec is the canonical source of architectural constraints. Humans can inspect it directly, but the supported mutation flow is:

```text
spec propose → spec diff → spec apply
```

### Strategic planning

The planning flow is phase-based rather than a single one-shot roadmap generator:

```bash
python -m src.infra.cli.main plan init
python -m src.infra.cli.main plan architect
python -m src.infra.cli.main plan review
python -m src.infra.cli.main plan status
```

## Configuration reference

Primary environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODE` | `dry-run` | Selects dry-run vs Redis/live runtime behavior |
| `AGENT_ID` | `agent-worker-001` | Worker identity |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection for real mode |
| `TASK_TIMEOUT_SECONDS` | `600` | Per-task runtime timeout |
| `ORCHESTRATOR_HOME` | `~/.orchestrator` | Root orchestrator state directory |
| `PROJECT_NAME` | `default` | Active project context |
| `ANTHROPIC_API_KEY` | empty | Claude / pi-anthropic runtime auth |
| `GEMINI_API_KEY` | empty | Gemini / pi-gemini runtime auth |
| `OPENROUTER_API_KEY` | empty | pi-openrouter runtime auth |
| `SOURCE_REPO_URL` | unset | Compatibility override for the source repository |

## Testing

Representative test entry points:

```bash
pytest
pytest tests/unit/infra/test_cli_new_commands.py
pytest tests/integration/test_e2e_dry_run.py
pytest tests/integration/test_e2e_full.py
```

The repository currently organizes tests under:

- `tests/unit`
- `tests/integration`

## Additional documentation

- `docs/architecture.md` — updated architecture and data-flow notes
- `roadmap.md` — current status summary and likely next areas of work
- `src/infra/logging/README.md` — details of the runtime logging subsystem
