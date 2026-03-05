# Agent Orchestrated Dev System — Agent-CLI Mode

A Python prototype implementing an agent orchestration system using **hexagonal architecture** and an **event-driven core**. Agents are stateless CLI runtimes supervised per-task, with all canonical state living in `workflow/`.

## Architecture

```
Interface Layer (CLI / REST)
        ↑
  Application Layer  (TaskManagerHandler, WorkerHandler, Reconciler)
        ↑
  Ports / Adapters   (TaskRepo, LeasePort, EventPort, GitWorkspace, AgentRuntime)
        ↑
  Domain Layer       (TaskAggregate, AgentProps, SchedulerService, LeaseService)
```

**Core principles:**
- **SSoT:** `workflow/` is canonical. Never bypass it.
- **Persist-first:** state is written + fsynced before any event is emitted.
- **No self-claiming:** only the Task Manager assigns tasks.
- **Stateless sessions:** fresh agent CLI session per task, no reuse.
- **Workspace isolation:** ephemeral git clone per task on branch `task/<task_id>`.

---

## Project Structure

```
src/
  core/
    models.py          # Pydantic v2 domain models (TaskAggregate, AgentProps, ...)
    ports.py           # Abstract port interfaces (hexagonal boundary)
    services.py        # Pure domain services (SchedulerService, LeaseService)
  app/
    handlers/
      task_manager.py  # Task Manager use-case (assign + lease + event)
      worker.py        # Worker use-case (execute, validate, commit)
    reconciler.py      # Periodic lease-expiry and stale-task recovery
  infra/
    fs/
      task_repository.py   # YAML FS adapter (atomic write + fsync)
      agent_registry.py    # JSON FS adapter
    redis_adapters/
      lease_adapter.py     # Redis Streams lease port
      lease_memory.py      # In-memory lease port (tests)
      event_adapter.py     # Redis Streams event port + in-memory stub
    git/
      workspace_adapter.py # Subprocess git + dry-run stub
    runtime/
      agent_runtime.py     # Subprocess agent CLI + dry-run stub
    factory.py             # Dependency injection wiring (AGENT_MODE env var)
  cli.py                   # Click CLI entry points

workflow/                  # Canonical SSoT (git-tracked)
  project.yaml
  agents/registry.json
  tasks/task-<id>.yaml
  logs/task-<id>/
  events/

tests/
  unit/test_domain.py          # Domain model + service unit tests
  integration/test_e2e_dry_run.py  # Full lifecycle acceptance tests
scripts/
  demo.py                      # End-to-end demo script
```

---

## Quick Start

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Redis 7+ (for real mode; not needed for dry-run)
- Docker (optional, for containerised sessions)

### Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Run the demo (dry-run, no Redis needed)

```bash
AGENT_MODE=dry-run python scripts/demo.py
```

Expected output shows a task moving from `created → assigned → in_progress → succeeded` with a stub commit SHA.

### Run tests

```bash
AGENT_MODE=dry-run pytest -v
```

All tests use dry-run adapters — no external dependencies required.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_MODE` | `dry-run` | `dry-run` (stubs) or `real` (Redis + subprocess) |
| `AGENT_ID` | `agent-worker-001` | Identity of this worker process |
| `REPO_URL` | `file:///tmp/test-repo` | Git repo to clone for workspaces |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `TASKS_DIR` | `workflow/tasks` | Task YAML directory |
| `REGISTRY_PATH` | `workflow/agents/registry.json` | Agent registry file |
| `TASK_TIMEOUT_SECONDS` | `600` | Max agent session runtime |
| `AGENT_BINARY` | `/usr/local/bin/agent-cli` | Agent CLI binary path (real mode) |

---

## Real Mode (Redis + Docker)

```bash
# Start Redis
docker compose up -d redis

# Register an agent
AGENT_MODE=real python -m src.cli register-agent \
  --agent-id agent-worker-001 \
  --name "Backend Worker" \
  --capabilities "backend_dev,python" \
  --version 1.2.0

# Start Task Manager (separate terminal)
AGENT_MODE=real python -m src.cli task-manager

# Start Reconciler (separate terminal)
AGENT_MODE=real python -m src.cli reconciler --interval 30

# Start RQ worker (separate terminal)
AGENT_MODE=real AGENT_ID=agent-worker-001 rq worker --url redis://localhost:6379/0 agent-tasks

# Create a task
AGENT_MODE=real python -m src.cli create-task workflow/tasks/task-3c9b.yaml

# Check status
AGENT_MODE=real python -m src.cli list-tasks
```

Or start all services via Docker Compose:

```bash
REPO_URL=https://github.com/your/repo AGENT_BINARY=/usr/local/bin/gemini-cli \
  docker compose up
```

---

## Task YAML Format

```yaml
task_id: task-3c9b
feature_id: feat-auth
title: "Implement POST /login"
description: |
  Add login endpoint that validates credentials and returns JWT.
agent_selector:
  required_capability: "backend_dev"
  min_version: ">=1.0.0"
execution:
  type: "code:backend"
  constraints:
    language: "python"
    framework: "fastapi"
  test_command: "pytest tests/test_auth.py -q"
  acceptance_criteria:
    - "All tests pass"
  files_allowed_to_modify:
    - "app/auth.py"
    - "tests/test_auth.py"
status: created
state_version: 1
retry_policy:
  max_retries: 2
  backoff_seconds: 30
  attempt: 0
```

---

## Task State Machine

```
created → assigned → in_progress → succeeded
                                 → failed → requeued → (assigned again)
                                          → canceled
any → canceled
succeeded → merged
```

Every transition: writes YAML + fsyncs, increments `state_version`, then emits event.

---

## Swapping Adapters

All ports are defined as ABCs in `src/core/ports.py`. To swap an adapter:

1. Implement the relevant port ABC.
2. Wire it in `src/infra/factory.py` (or inject directly in tests).

Example: swap Redis for NATS by implementing `EventPort` with a NATS client.

---

## Evaluation / Acceptance Criteria

| # | Criterion | How verified |
|---|---|---|
| 1 | All domain state persisted before event emission | `test_e2e_dry_run.py::test_created_to_succeeded` |
| 2 | Full lifecycle `created → succeeded` with commit info | same |
| 3 | Agent sessions per-task, cleaned up | WorkerHandler `finally` block |
| 4 | Forbidden-file edits cause `task.failed` | `test_forbidden_file_edit_causes_failure` |
| 5 | Reconciler requeues expired-lease tasks | `test_requeues_assigned_expired_lease` |
| 6 | All ports are interfaces, swappable by config | `factory.py` + `AGENT_MODE` |

---

## Metrics

Collected via `structlog` structured logs. For Prometheus, wrap adapters with counters:

- `task_success_total` / `task_failure_total`
- `task_latency_seconds` (histogram: created → completed)
- `task_retries_total`
- `forbidden_file_violations_total`
- `agent_session_timeouts_total`

Event journal: `workflow/events/evt-<ts>-<id>.json`
