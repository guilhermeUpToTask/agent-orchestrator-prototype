## Architecture overview

This project follows a layered, hexagonal-style architecture with three primary layers:

- **Domain (`src/core`)**: Pure business logic and domain state.
- **Application (`src/app`)**: Use-cases and orchestration, expressed in terms of domain types and ports.
- **Infrastructure (`src/infra`)**: Adapters for storage, messaging, runtimes, git, and wiring of the system.

### Layering and dependencies

- **Domain layer (`src/core`)**
  - Contains domain models and value objects in `core/models.py` such as `TaskAggregate`, `ExecutionSpec`, `RetryPolicy`, `TaskResult`, and `DomainEvent`.
  - Encodes the task state machine and invariants via methods like `assign`, `start`, `complete`, `fail`, `requeue`, `cancel`, and `mark_merged`.
  - Contains pure domain services in `core/services.py`, including:
    - `SchedulerService` for agent selection.
    - `LeaseService` for lease-expiry decisions.
    - `AnomalyDetectionService` for detecting stuck tasks, dead agents, and expired leases.
  - Defines ports (interfaces) in `core/ports.py` such as `TaskRepositoryPort`, `AgentRegistryPort`, `EventPort`, `LeasePort`, `GitWorkspacePort`, and `AgentRuntimePort` that abstract persistence, messaging, and runtimes.
  - **Does not depend on** `src/app` or `src/infra`, and avoids framework-specific imports.

- **Application layer (`src/app`)**
  - Implements use-cases and workflows using domain models and ports.
  - Key components:
    - `app/services/task_creation.py` (`TaskCreationService`): creates new `TaskAggregate` instances, persists them, and publishes `task.created` events.
    - `app/handlers/task_manager.py` (`TaskManagerHandler`): event-driven coordinator for task lifecycle, handling `task.created`, `task.requeued`, `task.completed`, and `task.failed`.
    - `app/handlers/worker.py` (`WorkerHandler`): orchestrates per-task execution, including workspace preparation, runtime session management, validation, tests, and persistence.
    - `app/reconciler.py` (`Reconciler`): watchdog that periodically scans tasks and emits events for stuck or anomalous tasks.
  - Application code is written **exclusively against ports and domain types**, and does not import concrete infra implementations.

- **Infrastructure layer (`src/infra`)**
  - Provides concrete implementations of the ports defined in `src/core` and wires the system together.
  - Key areas:
    - `infra/fs/*`: filesystem-based task repository and agent registry.
    - `infra/redis_adapters/*`: Redis-backed and in-memory implementations of `EventPort` and `LeasePort`.
    - `infra/git/workspace_adapter.py`: implementations of `GitWorkspacePort` (real git and dry-run).
    - `infra/runtime/*`: implementations of `AgentRuntimePort` (dry-run, Gemini, Claude Code, generic CLI).
    - `infra/config.py`: configuration for paths, URLs, and mode.
    - `infra/factory.py`: composition root that builds repositories, ports, runtimes, and high-level handlers (`TaskManagerHandler`, `WorkerHandler`, `Reconciler`, `TaskCreationService`).
  - **Depends on** `src/core` and `src/app` as expected for a composition root and adapters.

The intended dependency direction is:

```mermaid
flowchart LR
  core[core (models, services, ports)]
  app[app (handlers, use-cases)]
  infra[infra (adapters, factory)]

  infra --> app
  app --> core
```

### High-level data flow

- **Task creation**
  - A client uses `TaskCreationService` to create a `TaskAggregate`, which is persisted via `TaskRepositoryPort` and emits a `task.created` `DomainEvent`.
  - The `TaskManagerHandler` consumes `task.created`, selects an agent with `SchedulerService`, assigns the task, creates a lease, and emits `task.assigned`.

- **Task execution**
  - A worker process, using `WorkerHandler`, consumes `task.assigned`, validates the assignment, and prepares a git workspace via `GitWorkspacePort`.
  - `WorkerHandler` transitions the task to `IN_PROGRESS` with optimistic concurrency, builds an `ExecutionContext`, and delegates execution to a runtime via `AgentRuntimePort`.
  - After completion, it validates file modifications via `ExecutionSpec`, optionally runs tests, commits and pushes via `GitWorkspacePort`, persists the result, and emits `task.completed` or `task.failed`.

- **Recovery and retries**
  - The `Reconciler` periodically scans tasks through `TaskRepositoryPort`, using `AnomalyDetectionService` and `LeasePort`/`AgentRegistryPort` to detect stuck or unhealthy tasks.
  - For stuck pending tasks, it republishs `task.created` or `task.requeued`. For dead agents or expired leases, it transitions tasks to `FAILED` and emits `task.failed`.
  - The `TaskManagerHandler` reacts to `task.failed`, using the domain `RetryPolicy` on `TaskAggregate` to decide whether to requeue or cancel, emitting `task.requeued` or `task.canceled` accordingly.

### Boundary conventions

- New business rules around task lifecycle, retries, dependencies, and anomaly detection should be expressed in:
  - `src/core/models.py` (state machine, invariants, policies tied to `TaskAggregate`).
  - `src/core/services.py` (pure domain services and policy helpers).
- Application handlers in `src/app` should:
  - Orchestrate workflows and side effects by calling domain services and ports.
  - Avoid embedding low-level I/O (files, subprocesses, network) that can instead be expressed via new or existing ports.
- Infrastructure in `src/infra` should:
  - Implement ports and configuration decisions, but not duplicate or override domain rules.
  - Keep runtime adapters focused on translating `ExecutionContext` to external process calls and back to domain types.

