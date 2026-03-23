# Agent Orchestrator Prototype

A local Python prototype for **plan-driven software execution**. The center of the project is the planning layer: the `plan` workflow discovers requirements, proposes architecture, dispatches phase goals, and advances the project through review gates. Tasks, goals, workers, specs, and runtime adapters all exist to support that higher-level project plan.

## What the project is for

The main objective of this codebase is **project-level orchestration**, not just task execution.

The current system combines:

- **Strategic planning as the top-level workflow**: `plan init`, `plan architect`, `plan review`, `plan status`, and `plan decision` manage the lifecycle of a project plan.
- **Goal orchestration underneath the plan**: approved phases dispatch goals, and goals coordinate dependent task work and branch-level progress.
- **Task execution underneath goals**: workers execute assigned tasks in isolated workspaces, while the task manager and reconciler keep execution moving.
- **Project spec governance**: `project_spec.yaml` constrains architecture and dependency choices, and spec changes are staged and operator-approved.

If you want to understand the product from the top down, start with the **`plan` command group**.

## Current architecture snapshot

```text
plan workflow (primary operator interface)
  ↓
goals + project plan + spec governance
  ↓
task orchestration and worker execution
  ↓
runtimes, git workspaces, Redis/events, filesystem state
```

In code, that maps to:

```text
CLI (src/infra/cli)
  ↓
Application layer (src/app)
  - planner orchestration
  - goal orchestration
  - task handlers / use cases
  ↓
Domain layer (src/domain)
  - project plan, goals, tasks, specs, value objects
  ↓
Infrastructure layer (src/infra)
  - repositories, runtimes, Redis adapters, git, logging, GitHub
```

Key design choices:

- **Plan-first workflow**: the project plan is the main control loop for the system.
- **Project-scoped state** lives under `~/.orchestrator/projects/<project_name>/...`.
- **Domain-first boundaries** keep business rules in `src/domain` and I/O in `src/infra`.
- **Multiple runtime adapters** are supported per registered agent (`dry-run`, `claude`, `gemini`, `pi`).
- **Execution observability** is built in through runtime logging and persisted event journals.

## CLI entry points

The canonical CLI entry point is:

```bash
python -m src.infra.cli.main --help
```

A compatibility shim still exists, so this also works:

```bash
python -m src.cli --help
```

## CLI overview

### Top-level command groups

| Group | Commands | Purpose |
|---|---|---|
| `plan` | `init`, `architect`, `review`, `status`, `decision` | **Primary project workflow** |
| `goals` | `init`, `run`, `status`, `finalize`, `plan`*, `dispatch-roadmap`*, `sessions`* | Goal-level orchestration |
| `tasks` | `create`, `list`, `retry`, `delete`, `prune` | Low-level task management |
| `system` | `start`, `task-manager`, `worker`, `reconciler` | Long-running orchestration daemons |
| `agents` | `create`, `list`, `edit`, `delete` | Agent registry management |
| `spec` | `show`, `init`, `validate`, `propose`, `diff`, `apply` | Project-spec governance |
| `project` | `reset` | Active project reset |
| `init` | `--defaults` | Setup wizard / default config |

`*` Deprecated commands still exist under `goals` for backward compatibility, but the main planning interface is the `plan` group.

### The workflow hierarchy

Think of the CLI in this order:

1. **`plan`** decides what the project should do next.
2. **`goals`** represent phase-approved chunks of work created or unlocked by the plan.
3. **`tasks`** are the execution units inside goals.
4. **`system`** runs the daemons that carry out assignments and recovery.
5. **`agents`**, **`spec`**, and **`project`** support that lifecycle.

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
- `project_plan.yaml` — the persisted project plan and current phase state
- `planner_sessions/` — discovery / architecture / phase-review session records
- `project_spec.yaml` — architecture and dependency constraints used by validation and planning

## Plan-first quick start

This is the recommended way to understand and use the project.

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

### 3. Register at least one agent

A dry-run agent is the easiest starting point:

```bash
python -m src.infra.cli.main agents create \
  --agent-id dry-run-001 \
  --name "Dry Run Worker" \
  --capabilities code:backend \
  --runtime-type dry-run
```

### 4. Start the plan workflow

Begin with discovery:

```bash
python -m src.infra.cli.main plan init --dry-run
```

That stage gathers requirements, produces a project brief, and asks for operator approval.

After approving the brief, move into architecture planning:

```bash
python -m src.infra.cli.main plan architect --dry-run
```

That stage proposes decisions and phases. When approved, it can dispatch the first phase's goals.

### 5. Inspect project-plan state

```bash
python -m src.infra.cli.main plan status
```

Use this command to understand where the project is in the lifecycle before reaching for lower-level `goals` or `tasks` commands.

## Planning workflow

The **main point of the project** is this planning loop.

### 1. Discovery — `plan init`

```bash
python -m src.infra.cli.main plan init
```

What it does:

- starts or resumes a **discovery** session
- uses the interactive planner runtime to gather project requirements
- prints a generated **project brief**
- asks the operator whether to approve the brief

If approved, the plan moves from `discovery` to `architecture`.

Dry-run is supported:

```bash
python -m src.infra.cli.main plan init --dry-run
```

### 2. Architecture — `plan architect`

```bash
python -m src.infra.cli.main plan architect
```

This command only runs when the project plan is already in `architecture` state.

What it does:

- runs the architecture planning session
- shows pending architectural decisions
- shows proposed phases
- asks the operator which decisions to approve
- asks whether to approve the phase plan and start execution

When approved, the orchestrator:

- applies approved decisions
- applies any spec changes derived from those decisions
- transitions the plan into `phase_active`
- dispatches goals for the first approved phase

Dry-run is supported:

```bash
python -m src.infra.cli.main plan architect --dry-run
```

### 3. Phase review — `plan review`

```bash
python -m src.infra.cli.main plan review
```

This command only runs when the project plan is in `phase_review` state.

What it does:

- runs the review session for the completed phase
- prints lessons learned
- shows the next phase proposal, if one exists
- surfaces any pending decisions
- asks whether to continue with the next phase or mark the project done

Approval can either:

- transition the plan back to `phase_active` and dispatch the next phase's goals, or
- mark the project as `done`

Dry-run is supported:

```bash
python -m src.infra.cli.main plan review --dry-run
```

### 4. Inspecting the plan — `plan status`

```bash
python -m src.infra.cli.main plan status
```

This shows the persisted plan ID, status, vision, and known phases, including which phase is currently active.

### 5. Mid-phase questions — `plan decision`

```bash
python -m src.infra.cli.main plan decision "Should we split the API and worker services?"
```

This command exists in the CLI, but today it is still a placeholder rather than a fully implemented decision-approval workflow.

## Supporting workflows under the plan

### Goals

Goals are the layer directly below the plan. Approved phases dispatch or unlock goals, and goals in turn manage grouped task execution.

Core commands:

```bash
python -m src.infra.cli.main goals init <goal-file.yaml>
python -m src.infra.cli.main goals status
python -m src.infra.cli.main goals run
python -m src.infra.cli.main goals finalize <goal_id>
```

Deprecated compatibility commands still exist:

```bash
python -m src.infra.cli.main goals plan <user_input>
python -m src.infra.cli.main goals dispatch-roadmap <session_id>
python -m src.infra.cli.main goals sessions
```

Those three commands are legacy planning-era commands; prefer `plan init`, `plan architect`, and `plan status`.

### Tasks

Tasks are the execution units below goals. They are useful for operators and debugging, but they are not the main project-level entry point.

Representative commands:

```bash
python -m src.infra.cli.main tasks create \
  --title "Add health endpoint" \
  --description "Implement a basic health endpoint and tests" \
  --capability code:backend \
  --allow src/api/health.py \
  --allow tests/test_health.py \
  --test "pytest tests/test_health.py"

python -m src.infra.cli.main tasks list
python -m src.infra.cli.main tasks retry <task_id>
```

## Operating modes

### Dry-run mode

Default mode is `dry-run`.

Use it when you want to:

- exercise the planning workflow locally
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

Or boot everything from the active registry:

```bash
AGENT_MODE=real python -m src.infra.cli.main system start
```

`system start` reads the active agent registry, launches one worker per active agent, waits for heartbeats, and then starts the reconciler.

## Agent runtimes

The runtime factory currently supports these runtime types:

- `dry-run`
- `gemini`
- `claude`
- `pi`

Runtime-specific options are stored on each agent record in `runtime_config`, which allows multiple differently configured agents to coexist in the same registry.

## Project spec workflow

The project spec is the canonical source of architectural constraints used by planning and validation.

Supported mutation flow:

```text
spec propose → spec diff → spec apply
```

Representative commands:

```bash
python -m src.infra.cli.main spec show
python -m src.infra.cli.main spec init
python -m src.infra.cli.main spec validate --description "add redis cache"
python -m src.infra.cli.main spec propose --add-required fastapi
python -m src.infra.cli.main spec diff
python -m src.infra.cli.main spec apply
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
pytest tests/unit/app/usecases/test_planner_orchestrator.py
pytest tests/integration/test_e2e_dry_run.py
pytest tests/integration/test_e2e_full.py
```

The repository currently organizes tests under:

- `tests/unit`
- `tests/integration`

## Additional documentation

- `docs/architecture.md` — architecture, runtime workflows, and boundaries
- `roadmap.md` — current-state roadmap and likely next milestones
- `src/infra/logging/README.md` — runtime logging subsystem details
