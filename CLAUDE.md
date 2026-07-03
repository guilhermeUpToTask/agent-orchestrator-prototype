# CLAUDE.md - AIPOM / Agent Orchestrator

## 🚀 Build & Run Commands

### Backend (Python) — all commands run from `backend/`
- **Install**: `cd backend && uv pip install -e .[dev]` (or `pip install -e .[dev]`)
- **DB migrations**: `python -m src.infra.cli.main db upgrade` (DB lives under `ORCHESTRATOR_HOME`, default `~/.orchestrator`)
- **Run API**: `python -m src.infra.cli.main api start --port 8000`
- **Run Worker (Dry-Run)**: `AGENT_MODE=dry-run python -m src.infra.cli.main worker start`
- **CLI Entry Point**: `python -m src.infra.cli.main` (or `orchestrate` if installed) — commands: `db upgrade`, `api start`, `worker start`, `config get|set|list`, `plan list|show`, `seed demo [--stub | --provider … --model … --api-key-env …]`
- **Format & Lint**: `ruff check src tests --fix`
- **Type Check**: `mypy src` (zero errors, no excludes)
- **Test All**: `pytest`
- **Test Unit (fast)**: `pytest -m "not integration"`
- **Test Integration**: `pytest -m integration` (includes the SQLite truth-test parametrization)
- **Test real-LLM smoke** (cost-gated, never in normal CI): `pytest -m llm` with `REASONER_SMOKE_API_KEY` (+ optional `REASONER_SMOKE_BASE_URL` / `REASONER_SMOKE_MODEL`)

Environment: `ORCHESTRATOR_HOME` (state dir), `AGENT_MODE` (`dry-run` | `pi` | `claude` | `gemini`), `ORCHESTRATOR_MASTER_KEY` (Fernet key for the secret store), `PROJECT_REPO_DIR` (target repo for the git workspace), `ORCHESTRATOR_API_TOKEN` (control-plane auth; open when unset).

**The reasoner resolves via the providers catalog, NOT env vars** — config keys (scope `orchestrator`): `reasoner.mode` (`stub` default | `llm`), `reasoner.provider_id`, `reasoner.model_id`, `reasoner.temperature` (0.2), `reasoner.max_turns` (8). In `llm` mode `src/infra/reasoner/factory.py` fail-fasts (`REASONER_CONFIG_INVALID` → 422) and resolves the provider row (base_url + envelope-encrypted key) and model row; **stub mode never touches the secret store** (dry-run needs no master key). `orchestrate seed demo` seeds capabilities, the default agent, provider/model rows, and the config keys idempotently.

### Frontend (TypeScript / React / Vite)
- **Install**: `npm install`
- **Run Dev**: `npm run dev`
- **Build**: `npm run build` (tsc + vite)
- **Regenerate API types**: `npm run generate:api` (backend/scripts/export_openapi.py + openapi-ts → `src/types/generated/`). The plan DETAIL read model (the aggregate document) is hand-declared in `src/types/ui.ts` — keep it in sync with the domain.

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
- **DISCOVERY / REPLANNING** — conversational, MULTI-TURN with commit (`conversation.py`); invisible to workers. Each user message is one `reasoner.converse()` turn: a reply without goals keeps the conversation open (question turn, chat persisted); a reply WITH goals is the roadmap commit → ARCHITECTURE. User messages persist BEFORE the LLM call (they survive reasoner crashes). Chat history lives in `plan_chat_messages` (own short txns, never the plan UoW).
- **ARCHITECTURE** — a deliberate **no-LLM passthrough** (`PlanningHandler._architect`): the conversation already committed the user-agreed roadmap, so autonomous re-structuring is redundant; the phase stays in the frozen enum (REPLANNING re-enters through it, free crash checkpoint) and the handler is the seam if a real structuring pass returns.
- **ENRICHING** — the JIT step (`PlanningHandler._enrich`): ONE task-less goal per worker step; `reasoner.enrich_goal(plan, goal, capabilities)` breaks it into 1..N plain executable tasks; idempotent (a goal with tasks is never re-enriched), checkpointed goal-by-goal via `Signal.CONTINUE`; when no task-less goal remains, agents bind → AWAITING_REVIEW.
- **AWAITING_REVIEW / REVIEW** — human gates; ALWAYS pause; unblocked only by `approve` / `finish` / `replan` commands.
- **RUNNING** — the pull-scan execution loop (`ExecutionHandler`): two-transaction writes, check-before-act idempotency, durable backoff gate (`retry_not_before`), tolerant finalize for late results after a replan.

**The Reasoner port is exactly two methods** (`src/domain/ports/reasoner_port.py`): `converse(plan, history, message, mode) -> ReasonerReply{message, goals|None}` and `enrich_goal(plan, goal, capabilities) -> list[Task]`. Implementations: `StubReasoner` (deterministic `ask:` / `goal:/task: [caps: …]` grammar — drives dry-run and tests) and `OpenAIReasoner` (`src/infra/reasoner/openai_reasoner.py`) on the runtime package `src/infra/reasoner/runtime/` — an async tool-calling agent loop (`run_tool_session`) with terminal submit tools (`submit_goals`, `submit_tasks`), `{accepted:false, errors}` self-correction, AsyncOpenAI client with transient/permanent retry classification and the empty-choices guard, and a plan→markdown context renderer. Handlers re-validate ALL tool args (never trust provider schema enforcement); history replays as plain text (never provider transcripts).

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
- Chat: `POST /api/plans/{id}/discovery/message` and `/replanning/message` drive the conversational phases — both return `200 MessageResponse{reply, committed, phase}`; `GET /api/plans/{id}/chat` serves the persisted history. The chat reply travels in the HTTP response body (SSE carries only domain events — no dual-publish).
- Frontend routes: `/` (plan list + composer), `/plans/:id` (Overview / Goals canvas / Agents / Activity, with the chat panel and the two gate dialogs). SSE events are NAMED (`event: <type>`), so the client registers per-type listeners.

## 🧪 Testing Guidelines
1. **The truth test**: `tests/unit/orchestration/` runs the orchestration suite through the parametrized `env_factory` fixture — in-memory fakes AND the real SQLite UnitOfWork (`tests/support.py`). Crash-recovery / outbox-rollback / backoff-survives-crash passing on real SQLite is the proof transactional atomicity is real. Keep fake and real adapter semantics identical (detached aggregates, CAS shape, lease expiry).
2. **Unit tests**: domain invariants + use cases against `src/app/testing/fakes.py` (`InMemoryPlanRepository`, `DummyAgentRunner` scripted per task id, `FakeClock.advance()` for backoff/lease determinism).
3. **Integration tests** (`tests/integration/`, marked `integration`): real SQLite (`tmp_path` DBs), real git repos for workspace tests, `TestClient` for the API, scripted fake CLIs for the runner taxonomy. `test_full_cycle.py` drives all nine phases + the replan loop on the stub; `test_full_cycle_llm.py` drives the same walk through `OpenAIReasoner` on a scripted `FakeLLMClient` (`tests/fakes_llm.py`). Reasoner-runtime unit tests live in `tests/unit/reasoner/`. The real-provider smoke (`test_reasoner_smoke.py`) is behind marker `llm` + `REASONER_SMOKE_API_KEY`.
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
│   │   │                   #   navigation scan, RetryPolicy/FailureKind, repo ports,
│   │   │                   #   ports/ (Reasoner/AgentRunner/Workspace/EventSink/Clock)
│   │   ├── app/            # Use cases + handlers (Execution/Gate/Planning), ports.py
│   │   │                   #   (TaskFailed/Outbox/UnitOfWork/ChatStore + domain re-exports),
│   │   │                   #   run_worker loop, conversation turns, testing/fakes.py
│   │   ├── infra/          # SQLite (UoW/plan repo/outbox/chat/reference/secrets), git
│   │   │                   #   workspace, CLI agent runners + taxonomy, reasoner/
│   │   │                   #   (stub + OpenAIReasoner + runtime/ tool loop + factory),
│   │   │                   #   worker entrypoint, container (composition root), CLI
│   │   └── api/            # FastAPI: thin routers, ONE error map, SSE broker,
│   │                       #   outbox relay, security, request logging
│   ├── tests/              # support.py + fakes_llm.py + unit/orchestration (dual-backend)
│   │                       #   + unit/reasoner + integration
│   ├── alembic/            # migration chain (0001_core, 0002_reference, 0003_chat)
│   └── docs/               # INTEGRATION_GUIDE.md (frozen contracts), DESIGN_NOTES.md,
│                           #   adr-concurrency-lease.md
└── frontend/               # React/Vite on the thin API: plan list + /plans/:id shell,
                            #   chat panel, gates, 9-phase rail, goals canvas, SSE bridge
```
