# CLAUDE.md - AIPOM / Agent Orchestrator

## 🚀 Build & Run Commands

### Backend (Python) — all commands run from `backend/`
- **Install**: `cd backend && uv pip install -e .[dev]` (or `pip install -e .[dev]`)
- **Run API**: `python -m src.infra.cli.main system api --port 8000`
- **Run Full System (Dry-Run)**: `AGENT_MODE=dry-run python -m src.infra.cli.main system start`
- **CLI Entry Point**: `python -m src.infra.cli.main` (or `orchestrate` if installed)
- **Format & Lint**: `ruff check src tests --fix`
- **Type Check**: `mypy src`
- **Test All**: `pytest`
- **Test Unit**: `pytest tests/unit`
- **Test Integration**: `pytest tests/integration`
- **DB migrations**: `alembic upgrade head` (config DB lives under `ORCHESTRATOR_HOME`)

### Frontend (TypeScript / React / Vite)
- **Install**: `npm install`
- **Run Dev**: `npm run dev`
- **Build**: `npm run build`

## 🏗️ Architectural Invariants (Backend)

The backend follows a strict **Hexagonal / Clean Architecture**.

1. **Dependency Rule**: `domain` -> `app` -> `infra` & `api`.
   - **NEVER** import `app`, `infra`, or `api` modules inside `src/domain/`.
   - **NEVER** import `infra` inside `src/app/` (App uses Domain Ports, Infra provides Adapters).
2. **Aggregates & State Transitions**:
   - `TaskAggregate`, `GoalAggregate`, `ProjectPlan`, and `PlannerSession` are the absolute authorities on state.
   - **NEVER** mutate aggregate fields directly from Use Cases (e.g., `task.status = ...`). Always use transition methods (e.g., `task.start()`, `task.fail()`) which internally call `_bump()` to increment `state_version` and append to `history`.
3. **Optimistic Concurrency Control (CAS)**:
   - Use Cases must persist state using `update_if_version(id, aggregate, expected_version)`.
   - If a CAS conflict occurs (returns `False`), reload the aggregate and retry (typically `MAX_CAS_RETRIES = 5`).
4. **Event-Driven Flow**:
   - Aggregates don't emit events directly. Use Cases persist the aggregate, then call `EventPort.publish(DomainEvent(...))`.
   - Payloads in `DomainEvent` must be minimal (IDs and tiny metadata). Full state must be fetched from Repositories by the consumer.
5. **Configuration & Dependency Injection**:
   - Do NOT use `os.environ` deep in the code.
   - All config flows from `SettingsService` into `SettingsContext`.
   - `AppContainer` (in `src/infra/container.py`) lazy-loads and caches all Repositories, Ports, and Use Cases.
6. **Error Handling**:
   - Domain errors inherit from `DomainError` (or `ValueError`/`KeyError`).
   - CLI commands use the `@catch_domain_errors` decorator.
   - API endpoints rely on global exception handlers in `src/api/exceptions.py`. Do NOT scatter `try/except` blocks returning HTTP responses inside API routers.

## ⚛️ Architectural Invariants (Frontend)

1. **State Management**:
   - Global state, data fetching, and API calls are currently managed via Zustand (`plannerStore.ts`). 
   - *Note: Moving towards `@tanstack/react-query` for data fetching per roadmap.*
2. **Domain Parity**:
   - `src/types/domain.ts` strictly mirrors the Python backend schemas. 
   - *Note: Moving towards `openapi-ts` generation per roadmap.*
3. **Planning & Chat Flow**:
   - The frontend never calls LLMs directly. All chat messages are routed to `/api/plan/refine` or `/api/plan/discovery/message` depending on the `ProjectPlanStatus`.
4. **React Flow / DAGs**:
   - The task graph uses `@xyflow/react`. Nodes represent Tasks, grouped logically by Goals.
   - Layout calculation happens in `src/lib/layout.ts` using `dagre`.

## 📁 Repository Structure

```text
agent-orchestrator/
├── backend/                # Python backend (all backend commands run here)
│   ├── src/
│   │   ├── domain/         # Entities, Value Objects, Aggregates, Ports (Interfaces)
│   │   ├── app/            # Use Cases, Handlers, Services (Orchestration)
│   │   ├── infra/          # Adapters (Redis, SQLite/SQLAlchemy, FS, Git, GitHub, CLI)
│   │   └── api/            # FastAPI Routers, DTOs (Schemas), SSE Streaming
│   ├── tests/              # Unit and Integration test split
│   ├── alembic/            # SQLAlchemy migrations (+ alembic.ini)
│   ├── scripts/            # OpenAPI export and other tooling
│   └── pyproject.toml      # Python dependencies & Ruff/Mypy config
└── frontend/
    ├── src/
    │   ├── components/    # React components (PlanCanvas, ChatPanel, Toolbar)
    │   ├── store/         # Zustand global state
    │   ├── lib/           # API wrappers, layout math
    │   ├── types/         # openapi-ts generated types
    │   └── styles/        # Global CSS & Design Tokens
    └── package.json       # React / Vite dependencies
```

## 🧠 Key Concepts & Terminology
Planner Orchestrator: High-level strategic agent. Transitions the project through discovery, architecture, phase_active, phase_review, done.
Goal Orchestrator: Intermediate level. Listens to PR states (AWAITING_PR_APPROVAL, APPROVED, MERGED) and task branch merges.
Task Manager / Worker / Reconciler: Low-level daemons. Handle CREATED -> ASSIGNED -> IN_PROGRESS -> SUCCEEDED / FAILED.
Tactical JIT Planner: Triggered just-in-time when a Goal is unblocked. Generates TDD (Test Writer + Implementer) tasks dynamically.
Project Spec (project_spec.yaml): Read-only constraints. Mutated ONLY via orchestrate spec propose -> orchestrate spec apply (approval gate).


## 🔄 GitHub & PR Workflow Rules
The orchestrator has a strict boundary regarding Version Control and GitHub interactions:
1. **Goal Branches vs. Task Branches**: 
   - Workers execute tasks on `goal/<goal-name>/task/<task-id>`.
   - On task success, `GoalMergeTaskUseCase` merges the task branch into the `goal/<goal-name>` branch.
2. **PR Creation & Merging**:
   - The orchestrator creates a PR against `main` (or `base_branch`) when a Goal hits `READY_FOR_REVIEW`.
   - **Crucial:** The orchestrator *NEVER* merges PRs. Merging is done by humans (or a merge queue) via the GitHub UI.
   - `SyncGoalPRStatusUseCase` periodically polls the GitHub API to update `pr_checks_passed` and `pr_approved`.

## 📡 Observability & Logging
Do not use standard `print()` or Python's default `logging` module.
1. **Structured Logging (`structlog`)**:
   - Always instantiate: `log = structlog.get_logger(__name__)`.
   - Use namespaced, action-oriented event names as the first argument, followed by kwargs.
   - Example: `log.info("worker.agent_session_completed", task_id=task.task_id, success=True)`
2. **Telemetry & Tracing**:
   - App services should use `TelemetryService` to generate `TraceContext` (trace_id, span_id).
   - Telemetry events are emitted to Redis Streams (`telemetry:events`) and JSONL files.
3. **Live Streaming (`LiveLogger`)**:
   - Agent subprocess outputs (stdout/stderr) are intercepted by `LoggingRuntimeWrapper` and streamed to the terminal via `LiveLogger` for real-time operator feedback.

## 🔌 Frontend <-> Backend Communication
1. **Server-Sent Events (SSE)**:
   - When the backend mutates state via an API route, it must call `publish_sse("event.type", payload)` from `src.api.sse`.
   - The frontend Zustand store (`plannerStore.ts`) subscribes to `/api/events`. 
   - State updates in the UI should be driven by these SSE events (e.g., updating a node's status when `task.status_changed` is received), eliminating the need for HTTP polling.

## 🧪 Testing Guidelines
1. **Unit Tests (`tests/unit/`)**:
   - Focus on Domain invariants and Use Case logic.
   - Use `unittest.mock.MagicMock` for Repositories and Ports.
   - Use `InMemoryTaskRepo`, `InMemoryEventAdapter`, and `StubGitHubClient` where mocks become too verbose.
2. **Integration Tests (`tests/integration/`)**:
   - Use `fakeredis.FakeRedis()` for real Pub/Sub and TTL testing without requiring a live Redis server.
   - Always use the `tmp_path` pytest fixture for file I/O (`YamlTaskRepository`, Git workspaces).
   - Use `monkeypatch` to safely override `os.environ` or global variables (like `src.infra.logs_and_tests.LOG_BASE`).
3. **Factories & Fixtures**:
   - A `mock_container` fixture is available in `conftest.py` which patches `AppContainer.from_env` globally, protecting tests from accidental local environment reads.

## 🧹 Code Style & Types
- **Python**: `mypy src` must pass with zero errors. `src/domain` and `src/app` are fully strict; the adapter layers (`src/infra`, `src/api`) carry explicit, documented relaxations in `pyproject.toml` — tighten them over time, never loosen. Use `from __future__ import annotations`. Use `pydantic` `BaseModel` for DTOs and Value Objects. Use `@dataclass(frozen=True)` for immutable domain structures when Pydantic isn't strictly necessary.
- **TypeScript**: Strictly typed. Avoid `any`. Interfaces mapped to Backend DTOs must remain synchronized.