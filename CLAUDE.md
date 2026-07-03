# CLAUDE.md - AIPOM / Agent Orchestrator

## 🚀 Build & Run Commands

### Backend (Python) — all commands run from `backend/`
- **Install**: `cd backend && uv pip install -e .[dev]` (or `pip install -e .[dev]`)
- **DB migrations**: `python -m src.infra.cli.main db upgrade` (DB lives under `ORCHESTRATOR_HOME`, default `~/.orchestrator`)
- **Run API**: `python -m src.infra.cli.main api start --port 8000`
- **Run Worker (Dry-Run)**: `AGENT_MODE=dry-run python -m src.infra.cli.main worker start`
- **CLI Entry Point**: `python -m src.infra.cli.main` (or `orchestrate` if installed) — commands: `db upgrade`, `api start`, `worker start`, `config get|set|list`, `plan list|show`
- **Format & Lint**: `ruff check src tests --fix`
- **Type Check**: `mypy src` (zero errors, no excludes)
- **Test All**: `pytest`
- **Test Unit (fast)**: `pytest -m "not integration"`
- **Test Integration**: `pytest -m integration` (includes the SQLite truth-test parametrization)

Environment: `ORCHESTRATOR_HOME` (state dir), `AGENT_MODE` (`dry-run` | `pi` | `claude` | `gemini`), `ORCHESTRATOR_MASTER_KEY` (Fernet key for the secret store), `PROJECT_REPO_DIR` (target repo for the git workspace), `ORCHESTRATOR_API_TOKEN` (control-plane auth; open when unset).

### Frontend (TypeScript / React / Vite)
- **Install**: `npm install`
- **Run Dev**: `npm run dev`
- **Build**: `npm run build`
- ⚠ The frontend still targets the pre-integration API and is being re-pointed at the new thin API (roadmap Phase 4).

## 🏗️ Architectural Invariants (Backend)

The backend follows a strict **Hexagonal / Clean Architecture**.

1. **Dependency Rule**: `domain` -> `app` -> `infra` & `api`.
   - **NEVER** import `app`, `infra`, or `api` modules inside `src/domain/`.
   - **NEVER** import `infra` inside `src/app/` (App uses Ports; Infra provides Adapters). The in-memory fakes live in `src/app/testing/fakes.py`; infra re-exports what it shares (e.g. the dummy runner).
2. **The `Plan` aggregate is the single authority** (`src/domain/aggregates/planner_orchestrator.py`):
   - It owns the goal/task tree and is the ONLY caller of `Goal`/`Task` transition methods. **NEVER** mutate goal/task fields from use cases — go through the aggregate's guarded transitions (`start_task`, `complete_task`, `enter_review`, `begin_replanning`, `commit_replanned_goals`, ...). Illegal transitions raise `InvalidTransitionError`.
   - **Navigation is derived, never stored**: `next_action(goals, now)` re-scans statuses every tick; there is no cursor to desync. `now` is always injected — the domain never reads a clock.
   - 🔒 The domain is FROZEN (roadmap Phase 0). Additions require a deliberate un-freeze.
3. **Optimistic Concurrency (CAS)**: use cases call `plan.bump_version()` then `uow.plans.save(plan)`; the store rejects when `stored.version >= incoming.version` with `StaleVersionError` (worker-vs-edit race → API 409).
4. **Transactional outbox**: state changes and their `DomainEvent`s are written in the SAME `with uow:` transaction (`uow.plans.save(...)` + `uow.outbox.add(event)`). Event payloads are minimal (IDs + tiny metadata). The API's **outbox relay** delivers rows to SSE at-least-once; consumers dedup on `event_id`. Side effects (agent runs, LLM calls) happen OUTSIDE transactions; finalize transactions re-read and re-guard.
5. **The lease replaces the reconciler**: `claim_one_unit` / `heartbeat` / `release` on the plan row. Only ARCHITECTURE / ENRICHING / RUNNING are claimable (the driver model); a dead worker's lease expires and any worker reclaims from persisted state. The worker tick reports *progress*, not claiming (no-progress → sleep).
6. **Configuration & DI**: the environment is read ONLY in `AppContainer` (`src/infra/container.py`, the composition root) — never deep in the code. All adapters hang off the container as cached properties; `new_unit_of_work()` per worker/request (a UoW is not thread-safe).
7. **Error Handling**: domain errors subclass `DomainError` with a stable `code`; the CLI uses `@catch_domain_errors`; the API maps codes to HTTP statuses in ONE table (`src/api/exceptions.py::_STATUS_BY_CODE`). Do NOT scatter try/except returning HTTP responses inside routers, and do NOT add blanket `KeyError`/`ValueError` handlers.

## 🧠 Key Concepts & Terminology

**The nine-phase machine** (`PlanPhase`):
`DISCOVERY → ARCHITECTURE → ENRICHING → AWAITING_REVIEW → RUNNING → REVIEW → DONE`, with `REPLANNING` re-entering ARCHITECTURE and `FAILED` terminal.
- **DISCOVERY / REPLANNING** — conversational (chat/API-driven via `conversation.py`); invisible to workers.
- **ARCHITECTURE / ENRICHING** — autonomous reasoner steps (`PlanningHandler`; the `Reasoner` port — currently `StubReasoner`, real LLM per roadmap 2.5).
- **AWAITING_REVIEW / REVIEW** — human gates; ALWAYS pause; unblocked only by `approve` / `finish` / `replan` commands.
- **RUNNING** — the pull-scan execution loop (`ExecutionHandler`): two-transaction writes, check-before-act idempotency, durable backoff gate (`retry_not_before`), tolerant finalize for late results after a replan.

**The replan loop (append-only)**: `request_replan` (mid-RUNNING chat or REVIEW) skips PENDING work → REPLANNING; `replanning_message` commits the new goal set — finalize-abandon closes leftovers, goals append after history, `iteration` increments. Prior DONE goals are never touched: they are history AND re-plan context.

**`apply_edit` ≠ `request_replan`**: surgical manual edit (status-guarded, capability IDs validated, requirements-edit re-runs `match_agent`, `RebindTaskAgent` = explicit override) vs holistic conversational re-plan.

**Shared failure taxonomy** (`FailureKind`): `connection_error | rate_limit | timeout | tool_error` retryable; `token_limit | auth_error` terminal. Produced by the CLI runners (`src/infra/runtime/taxonomy.py`) AND the dry-run dummy, so dry-run exercises production retry paths.

## 🔄 Git Workspace Rules
- Workers execute each attempt in a **worktree on `task/<task_id>/a<attempt>`**, branched off **`plan/<plan_id>`**.
- **commit** = `--no-ff` merge into the plan branch; **discard** = worktree + branch deleted — the rollback: a failed attempt leaves zero trace and retries begin clean (stateless task execution).
- `main` is never touched by plan work. GitHub PR output is DEFERRED (stub seam behind the Workspace port); the orchestrator never merges PRs.

## 📡 Observability & Logging
Do not use `print()` or the stdlib `logging` module.
1. **Structured logging (`structlog`)**: `log = structlog.get_logger(__name__)`; namespaced action-oriented event names, e.g. `log.info("workspace.committed", task_branch=..., plan_branch=...)`.
2. **Two event streams, one delivery path**:
   - **outbox** (coarse domain events, transactional with state) → relay → SSE (`/api/events`) — the system feed.
   - **agent_events** (fine-grained runtime telemetry, best-effort, own connection, `INSERT OR IGNORE` dedup) → relay tail → SSE `"agent.event"` — the live agent feed.
3. **Never log secrets**: keys live envelope-encrypted in the `secrets` table (`api_key_ref` URIs only); `resolve()` in `secret_store.py` is the single decryption point.

## 🔌 Frontend <-> Backend Communication
- Mutations write outbox events in the state transaction; the **relay** (not the routers) publishes them to the `SSEBroker`. Routers never call the broker directly.
- The frontend subscribes to `GET /api/events` and dedups on `event_id` (delivery is at-least-once).
- Chat: `POST /api/plans/{id}/discovery/message` and `/replanning/message` drive the conversational phases.

## 🧪 Testing Guidelines
1. **The truth test**: `tests/unit/orchestration/` runs the orchestration suite through the parametrized `env_factory` fixture — in-memory fakes AND the real SQLite UnitOfWork (`tests/support.py`). Crash-recovery / outbox-rollback / backoff-survives-crash passing on real SQLite is the proof transactional atomicity is real. Keep fake and real adapter semantics identical (detached aggregates, CAS shape, lease expiry).
2. **Unit tests**: domain invariants + use cases against `src/app/testing/fakes.py` (`InMemoryPlanRepository`, `DummyAgentRunner` scripted per task id, `FakeClock.advance()` for backoff/lease determinism).
3. **Integration tests** (`tests/integration/`, marked `integration`): real SQLite (`tmp_path` DBs), real git repos for workspace tests, `TestClient` for the API, scripted fake CLIs for the runner taxonomy. `test_full_cycle.py` drives all nine phases + the replan loop.
4. Always use `tmp_path` for file I/O and `monkeypatch` for env vars. No Redis anywhere (the claim path is the SQLite lease; Redis is roadmap Phase 3).

## 🧹 Code Style & Types
- **Python**: `mypy src` must pass with zero errors and NO exclude list; `src/domain` and `src/app` are fully strict, `src/infra`/`src/api` carry the documented relaxations in `pyproject.toml` — tighten over time, never loosen. Use `from __future__ import annotations`. Pydantic `BaseModel` for DTOs/VOs/entities; `@dataclass(frozen=True)` for immutable structures where Pydantic isn't needed.
- **TypeScript**: strictly typed, no `any`.

## 📁 Repository Structure

```text
agent-orchestrator/
├── backend/
│   ├── src/
│   │   ├── domain/         # FROZEN core: Plan aggregate (9-phase machine), Goal/Task,
│   │   │                   #   navigation scan, RetryPolicy/FailureKind, repo ports
│   │   ├── app/            # Use cases + handlers (Execution/Gate/Planning), ports.py,
│   │   │                   #   run_worker loop, conversation turns, testing/fakes.py
│   │   ├── infra/          # SQLite (UoW/plan repo/outbox/reference/secrets), git
│   │   │                   #   workspace, CLI agent runners + taxonomy, stub reasoner,
│   │   │                   #   worker entrypoint, container (composition root), CLI
│   │   └── api/            # FastAPI: thin routers, ONE error map, SSE broker,
│   │                       #   outbox relay, security, request logging
│   ├── tests/              # support.py + unit/orchestration (dual-backend) + integration
│   ├── alembic/            # fresh migration chain (0001_core, 0002_reference)
│   └── docs/               # INTEGRATION_GUIDE.md (frozen contracts), DESIGN_NOTES.md,
│                           #   adr-concurrency-lease.md
├── frontend/               # React/Vite (re-pointing at the thin API is roadmap Phase 4)
└── MASTER_ROADMAP_FINAL.md # the governing roadmap
```
