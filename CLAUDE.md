# CLAUDE.md - AIPOM / Agent Orchestrator

## 🚀 Build & Run Commands

### Backend (Python) — all commands run from `backend/`
- **Install**: `cd backend && uv pip install -e .[dev]` (or `pip install -e .[dev]`)
- **DB migrations**: `python -m src.infra.cli.main db upgrade` (DB lives under `ORCHESTRATOR_HOME`, default `~/.orchestrator`)
- **Run API**: `python -m src.infra.cli.main api start --port 8000`
- **Run Worker**: `python -m src.infra.cli.main worker start` (dry-run by default — the config key `agent_runner.mode` selects the runtime, NOT an env var)
- **CLI Entry Point**: `python -m src.infra.cli.main` (or `orchestrate` if installed) — commands: `db upgrade`, `api start`, `worker start`, `config get|set|list`, `plan list|show`, `seed demo [--stub | --provider … --model … --api-key-env …]`
- **Format & Lint**: `ruff check src tests --fix`
- **Type Check**: `mypy src` (zero errors, no excludes)
- **Test All**: `pytest`
- **Test Unit (fast)**: `pytest -m "not integration"`
- **Test Integration**: `pytest -m integration` (includes the SQLite truth-test parametrization)
- **Test real-LLM smoke** (cost-gated, never in normal CI): `pytest -m llm` with `REASONER_SMOKE_API_KEY` (+ optional `REASONER_SMOKE_BASE_URL` / `REASONER_SMOKE_MODEL`)

Environment: `ORCHESTRATOR_HOME` (state dir), `ORCHESTRATOR_MASTER_KEY` (Fernet key for the secret store), `ORCHESTRATOR_API_TOKEN` (auth for EVERY operation except `GET /health`; open when unset; `GET /api/events` also accepts it as `?token=` because EventSource cannot send headers). There is NO `AGENT_MODE` env var — runtime selection lives in SQLite, and **`PROJECT_REPO_DIR` is not read by anything** (locked by `tests/unit/test_fixture_docs_contract.py`): a plan reaches a repository through its `ProjectDefinition.repo_url`, validated on write by `src/infra/git/repository_binding.py`, and a project without one gets a scratch repo it is told about.

**The reasoner resolves via the providers catalog, NOT env vars** — config keys (scope `orchestrator`): `reasoner.mode` (`stub` default | `llm`), `reasoner.provider_id`, `reasoner.model_id`, `reasoner.temperature` (0.2), `reasoner.max_turns` (8). In `llm` mode `src/infra/reasoner/factory.py` fail-fasts (`REASONER_CONFIG_INVALID` → 422) and resolves the provider row (base_url + envelope-encrypted key) and model row; **stub mode never touches the secret store** (dry-run needs no master key). `orchestrate seed demo` seeds capabilities, the default agent, provider/model rows, and the config keys idempotently.

**The agent runner resolves via the AGENT REGISTRY + the providers catalog** (`src/infra/runtime/factory.py`) — the config key `agent_runner.mode` (`dry-run` default | `real`, plus `agent_runner.timeout_seconds` 600) picks the global mode; `dry-run` is the `DummyAgentRunner` and never touches the secret store. In `real` mode the `CatalogAgentRunner` resolves **per task, per run** from the bound `AgentSpec`: `runtime_type` (`pi` default | `claude` | `gemini` | `dry-run`) picks the CLI runtime, `provider_id`/`model_id` rows supply the envelope-encrypted key and model string (pi's backend derives from the provider id/name against `PI_BACKEND_ENV_VAR`). A broken binding raises `TaskFailed(AUTH_ERROR)` (terminal); write-time referential checks + `AGENT_RUNNER_CONFIG_INVALID` → 422; `GET /api/runner/status` reports mode/bindings/binary probes (`dependency_checker.py`) and the worker warns at boot in real mode. Providers/models bound to an agent are delete-guarded (409).

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
2. **Plan disposal is a repository command, not a transition**: the cyclic root
   is never terminal and a project owns exactly one long-lived `Plan`, so
   "finished with this plan" has no in-domain representation.
   `DELETE /api/plans/{id}` (`delete_plan`) is refused with 409 `PLAN_BUSY` while
   a worker holds a live lease. Every plan-scoped table declares
   `ON DELETE CASCADE` (migration 0015) so one delete leaves no orphan — a new
   plan-scoped table must too, and `test_delete_plan_leaves_nothing.py` fails if
   it does not.
3. **The `Plan` aggregate is the single authority** (`src/domain/aggregates/planner_orchestrator.py`):
   - It owns the goal/task tree and is the ONLY caller of `Goal`/`Task` transition methods. **NEVER** mutate goal/task fields from use cases — go through the aggregate's guarded transitions (`start_task`, `complete_task`, `enter_review`, `begin_replanning`, `commit_replanned_goals`, ...). Illegal transitions raise `InvalidTransitionError`.
   - **Navigation is derived, never stored**: `next_action(goals, now)` re-scans statuses every tick; there is no cursor to desync. `now` is always injected — the domain never reads a clock.
   - 🔒 The domain is FROZEN (roadmap Phase 0). Additions require a deliberate, decision-logged un-freeze — the full, authoritative list lives in [`docs/decisions/decision-log.md`](docs/decisions/decision-log.md) and currently runs through **un-freeze #19** (decision 60, 2026-07-28: a plan is cyclic before its cycle exists — `_is_cyclic`, not `active_cycle is not None`, decides whether the pause guards consult the legacy phase). The pivotal one is **#4 — [ADR-003](docs/decisions/adr-003-cyclic-project-plan-lifecycle.md) (decision 43, 2026-07-14): the cyclic `ProjectPlan` lifecycle**, which superseded the terminal nine-phase machine (see Key Concepts below). Earlier un-freezes: **#1** agent-registry runtime resolution (2026-07-05); **#2** the planning backoff gate (2026-07-08); **#3** the pause gate + recoverable auto-pause + editable-while-paused (2026-07-09). Do not re-summarize the whole list here — link the decision log.
4. **Optimistic Concurrency (CAS)**: use cases call `plan.bump_version()` then `uow.plans.save(plan)`; the store rejects when `stored.version >= incoming.version` with `StaleVersionError` (worker-vs-edit race → API 409).
5. **Transactional outbox**: state changes and their `DomainEvent`s are written in the SAME `with uow:` transaction (`uow.plans.save(...)` + `uow.outbox.add(event)`). Event payloads are minimal (IDs + tiny metadata). The API's **outbox relay** delivers rows to SSE at-least-once; consumers dedup on `event_id`. Side effects (agent runs, LLM calls) happen OUTSIDE transactions; finalize transactions re-read and re-guard.
6. **The lease replaces the reconciler**: `claim_one_unit` / `heartbeat` / `release` on the plan row. A plan is claimable when its root `PlanStatus` is `running` and it is bound to a project, not paused, and unclaimed-or-lease-expired (`_CLAIM_SQL`: `status='running' AND project_id IS NOT NULL AND paused=0 AND pause_requested=0`); a dead worker's lease expires and any worker reclaims from persisted state. Mid-action heartbeats renew the lease; the worker tick reports *progress*, not claiming (no-progress → sleep). Startup reconciliation closes stale RUNNING attempt rows only when NEITHER the plan claim NOR the owning goal's lease is live — attempts are created by goal workers, which do not hold the plan claim, so checking the plan alone let a second worker's startup abandon a live attempt's ledger row — and it never invents task outcomes.
7. **Configuration & DI**: the environment is read ONLY in `AppContainer` (`src/infra/container.py`, the composition root) — never deep in the code. All adapters hang off the container as cached properties; `new_unit_of_work()` per worker/request (a UoW is not thread-safe).
8. **Error Handling**: domain errors subclass `DomainError` with a stable `code`; the CLI uses `@catch_domain_errors`; the API maps codes to HTTP statuses in ONE table (`src/api/exceptions.py::_STATUS_BY_CODE`). Do NOT scatter try/except returning HTTP responses inside routers, and do NOT add blanket `KeyError`/`ValueError` handlers.

## 🧠 Key Concepts & Terminology

**The cyclic project-plan lifecycle** is the current authority ([ADR-003](docs/decisions/adr-003-cyclic-project-plan-lifecycle.md) / decision 43, un-freeze #4, accepted 2026-07-14; full detail in [`docs/architecture/plan-lifecycle.md`](docs/architecture/plan-lifecycle.md)). A `ProjectDefinition` owns exactly one long-lived `Plan` (the root aggregate — the class is still named `Plan` in `planner_orchestrator.py`) bound to an immutable `project_id`. **The root is never terminal.** Its only persisted lifecycle status is `PlanStatus` = `running | paused | waiting | blocked | idle`; `activity` is **derived** (open intent/draft/gate/block, active cycle, earliest non-terminal goal, current task), never a second stored enum or cursor. The API also serves `status_reason` + `legal_actions`; the frontend renders those facts rather than rebuilding transition rules.

Finite delivery work lives in a `Cycle`:
- **Intent** — conversation (`converse`, multi-turn, `conversation.py`) normalizes a brief into a versioned `IntentProposal`. User messages persist BEFORE the LLM call (they survive reasoner crashes); chat history lives in `plan_chat_messages` (own short txns, never the plan UoW). An exact-revision `ReviewGate` approves/edits/cancels it.
- **Architecture** — a **real reasoner session** (`architect_cycle`) submits an ordered `CycleDraft` with stable local goal keys and real dependency edges (forward edges rejected — stable position is the scheduling barrier). A second exact-revision gate approves it; approval atomically activates a `Cycle`.
- **Enrichment** — JIT, head-goal only (`enrich_goal_contract`): freezes a `GoalContract` + ordered `TaskContract`s (`execution_contracts.py`). Every task declares stable criteria, scope, capabilities, commands, and one verification mode of `tdd | characterization | executable_check`, revision-bound to `TestBundle` / `VerificationEvidence`.
- **Execution** — the pull-scan loop (`ExecutionHandler`, `navigation.py`): only the earliest non-terminal goal by stable position advances; a failed/backing-off head task, an unmet dependency, a gate, a block, or a pause request blocks later goals. Two-transaction choreography, check-before-act idempotency, durable backoff gate (`retry_not_before`, **strict in-goal order**), tolerant finalize for late results after a replan. Agent output is a **candidate only** — protected tests, scope, branch integrity, and evidence are independently verified before integration.
- **Completion & publication** — after all goal work is accepted, cycle verification opens one publication gate; dispositions are `open_pr | merge | retain_branch | discard` (a non-discard requires a recorded output reference). Recording it returns the root to IDLE.

**Pause / retry / failure** — pause is graceful: `pause_requested` blocks new claims immediately, an active atomic run finalizes, then the root settles PAUSED. `resume()` removes only a manual pause (never retries, rewinds identity, clears backoff, or resolves a block). Targeted **retry** names one failed task; **block resolution**, **`edit_task`**, and **`start_replan`** are separate explicit commands. **Provider capacity is waiting, not blocking**: a rate limit, quota, or connection failure opens a `RuntimeCircuit` and the work waits on automatic backoff with a single-flight half-open probe — bounded by WALL-CLOCK ceilings (`execution.provider_outage_ceiling_seconds` 6h, `execution.provider_daily_quota_ceiling_seconds` 26h), never by an attempt count, and inside the ceiling capacity failures bypass the per-task retry budget. Account-level limits (quota/daily) key a PROVIDER-WIDE circuit; upstream-level ones (concurrency/unclassified) key per-model — a `REQUEST_CONCURRENCY` refusal opens no circuit at all and just requeues that one task, on the plan's ordinary backoff curve rather than the patient 4x rate-limit one (`capacity_backoff_seconds`: only a positively identified concurrency refusal is impatient; an unclassified scope keeps patience). A provider admission gate (`execution.provider_max_inflight`, overridable per provider/model row — un-freeze #16) declines to start an attempt that would exceed the provider's in-flight ceiling, and selection routes a goal to another capability-satisfying agent whose provider is free (tier-ordered via `AgentSpec.model_role`, never mutating the persisted binding). Only past a ceiling does capacity escalate. A rejected CANDIDATE is not terminal either (un-freeze #17): `VERIFICATION_ERROR` left `non_retryable_kinds` and is capped by `RetryPolicy.kind_attempt_ceiling` (2) — a ceiling, the inverse of #15's per-kind floor, so it survives a higher operator budget. The retry only means something because `build_task_prompt` now renders the orchestrator's own rejection reasons (`src/app/agent_feedback.py` classifies which failures are agent-repairable; a promotion race or a provider limit never reaches a prompt), and because that ceiling counts attempts of the SAME kind — capacity failures used to spend it. Past it, an unsatisfiable contract is repaired in place (`src/app/contract_repair.py`, deterministic, bounded at 2, recorded) and a transient goal merge is re-attempted (`src/app/promotion_failures.py`, fail-closed classification) before anyone is asked. Other exhausted/permanent failure opens a structured `PlanBlock` (stage, goal/task/revision/run identity, evidence refs, operator-safe explanation, legal actions). For a plan with an active cycle, an execution-time block routes into `Plan.goal_blocks` (per goal, domain unfreeze #14) rather than the legacy plan-wide scalar `block` — one goal blocking never stops an independent sibling goal, and the root only settles **BLOCKED** once every non-terminal goal is blocked or transitively depends on one that is (`Plan._recompute_cyclic_status`); there is no terminal FAILED root either way. Every run has a globally unique `run_id`, monotonic absolute attempt number, and separate retry-cycle counter; long actions renew the lease; finalization revalidates claim/run/version/revision before any merge.

**Replan is source-preserving**: `start_replan` opens a new versioned `IntentProposal`; the source cycle stays visible and immutable while the proposal and a side-by-side `CycleDraft` are reviewed. Completed source work is supplied to the reasoner as context and must not be recreated; activation alone atomically supersedes the source cycle. (`apply_edit`/`edit_task` are surgical, status-guarded manual edits that invalidate revision-bound evidence and re-bind via the registry — distinct from a holistic conversational replan.)

**Blocks are the last resort, and `src/app/block_policy.py` is the one table that says so** — per kind: whether it `requires_human` and which resolutions it may advertise. Call sites may narrow (a task that is not FAILED cannot be retried) but never widen, and a test asserts every advertised resolution is actually accepted by its endpoint: a block whose only obvious next step 422s is worse than one that offers nothing.

**The Reasoner port has four purpose-specific transforms** (`src/domain/ports/reasoner_port.py`): `converse` (brief → `IntentCandidate`), `architect_cycle` (approved intent → stable-key `GoalOutline` DAG), `enrich_goal_contract` (head goal → frozen `GoalContract` + tasks, JIT), and `enrich_goal` (a **quarantined legacy-compat transform for pre-cyclic plans only**). It reads, never persists — callers own the transaction and the phase transition. Implementations: `StubReasoner` (deterministic grammar — drives dry-run and tests) and the OpenAI-compatible reasoner (`src/infra/reasoner/`) on the `runtime/` tool-calling loop with purpose-specific read tools plus exactly one submission tool per session (`submit_intent_proposal`, `submit_cycle_draft`, `submit_goal_contract`) that produce DTOs only. Handlers re-validate ALL tool args (never trust provider schema enforcement); history replays as plain text (never provider transcripts). The planner is no longer blind: bounded read-only repository tools (`RepositoryReader` -> `GitRepositoryReader`, a COMMITTED ref, never the working tree) plus an inline situation brief, and submission-time rejection of a contract nothing could satisfy — a scope matching no path, a command naming a near-twin of a real file, or a strategy whose test-authoring stage its own scope forbids. A failed planning session is no longer forgotten: what it produced is written to `planning_artifacts` (migration 0014, own transactions like `ChatStore`) and replayed into the retry, gated by an input fingerprint so an edit or replan discards it.

**Shared failure taxonomy** (`FailureKind`): `connection_error | rate_limit | timeout | tool_error` retryable; `token_limit | auth_error` terminal. Produced by the CLI runners (`src/infra/runtime/taxonomy.py`) AND the dry-run dummy, so dry-run exercises production retry paths. Verification-command infrastructure exits (126/127 → retryable `TOOL_ERROR`) are classified distinctly from a test verdict — `ExecutionHandler._raise_on_infrastructure_exit`, the single seam for that check.

**Legacy compatibility (NOT current)**: the nine-phase `PlanPhase` machine (`DISCOVERY → ARCHITECTURE → ENRICHING → AWAITING_REVIEW → RUNNING → REVIEW → DONE`, plus `REPLANNING`/`FAILED`) survives ONLY as a read/transition **compatibility projection** for migrated rows and existing clients (migration 0009 maps phase→status; unbound legacy plans quarantine as BLOCKED with a `project_binding` block, restored via `POST /api/plans/{id}/project-binding`). It is never the authority for a plan with an active cycle — do not describe it as the live model. See [`docs/architecture/known-issues.md`](docs/architecture/known-issues.md).

## 🔄 Git Workspace Rules
- Git staging is **project default branch → `plan/<plan_id>` → `cycle/<cycle_id>` → `goal/<goal_id>` → `task/<task_id>/a<attempt>`** (`project_workspace.py`; `workspace.py` holds the task-level primitive). Workers execute each attempt in a worktree on the task branch; **only independently verified work moves upward** a level — a goal branch never reaches the cycle branch until every task is DONE with accepted revision-bound evidence.
- **commit** = `--no-ff` merge one level up; **discard** = worktree + branch deleted — the rollback: a failed attempt leaves zero trace and retries begin clean (stateless task execution).
- The repository's detected default branch is never touched by plan work. One output disposition (`open_pr | merge | retain_branch | discard`) is recorded per cycle; GitHub PR output is DEFERRED (stub seam behind the Workspace port — no authenticated forge/PR-write port exists yet); the orchestrator never merges external PRs.

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
- Frontend routes: `/` (plan list + composer), `/plans/:id` (Overview / Goals canvas / Agents / Activity, with the chat panel, the intent/draft/publication review-gate dialogs, and block-resolution controls). SSE events are NAMED (`event: <type>`), so the client registers per-type listeners. The status/gate/block surface is driven by backend `status`/`activity`/`legal_actions`; some secondary panels still render legacy phase history (see [`docs/architecture/frontend.md`](docs/architecture/frontend.md)).

## 🧪 Testing Guidelines
1. **The truth test**: `tests/unit/orchestration/` runs the orchestration suite through the parametrized `env_factory` fixture — in-memory fakes AND the real SQLite UnitOfWork (`tests/support.py`). Crash-recovery / outbox-rollback / backoff-survives-crash passing on real SQLite is the proof transactional atomicity is real. Keep fake and real adapter semantics identical (detached aggregates, CAS shape, lease expiry).
2. **Unit tests**: domain invariants + use cases against `src/app/testing/fakes.py` (`InMemoryPlanRepository`, `DummyAgentRunner` scripted per task id, `FakeClock.advance()` for backoff/lease determinism).
3. **Integration tests** (`tests/integration/`, marked `integration`): real SQLite (`tmp_path` DBs), real git repos for workspace tests, `TestClient` for the API, scripted fake CLIs for the runner taxonomy. `test_default_cyclic_execution.py` drives the canonical cyclic walk (intent → draft → cycle → enrichment → execution → publication) on the stub; `test_full_cycle_llm.py` drives a walk through the OpenAI-compatible reasoner on a scripted `FakeLLMClient` (`tests/fakes_llm.py`). (`test_full_cycle.py` is the legacy nine-phase walk, now skipped/superseded — see its skip reason.) Cyclic orchestration unit tests live in `tests/unit/orchestration/test_cyclic_project_plan.py` + `test_cyclic_worker.py`; reasoner-runtime unit tests in `tests/unit/reasoner/`. The real-provider smoke (`test_reasoner_smoke.py`) is behind marker `llm` + `REASONER_SMOKE_API_KEY`.
4. Always use `tmp_path` for file I/O and `monkeypatch` for env vars. No Redis anywhere (the claim path is the SQLite lease; Redis is roadmap Phase 3).

## 🧹 Code Style & Types
- **Python**: `mypy src` must pass with zero errors and NO exclude list; `src/domain` and `src/app` are fully strict, `src/infra`/`src/api` carry the documented relaxations in `pyproject.toml` — tighten over time, never loosen. Use `from __future__ import annotations`. Pydantic `BaseModel` for DTOs/VOs/entities; `@dataclass(frozen=True)` for immutable structures where Pydantic isn't needed.
- **TypeScript**: strictly typed, no `any`.

## 📁 Repository Structure

```text
agent-orchestrator/
├── backend/
│   ├── src/
│   │   ├── domain/         # FROZEN core: Plan aggregate (cyclic ProjectPlan lifecycle;
│   │   │                   #   Cycle/IntentProposal/CycleDraft/ReviewGate/PlanBlock +
│   │   │                   #   Goal/Task/GoalContract/TaskContract), navigation scan,
│   │   │                   #   RetryPolicy/FailureKind, repo ports,
│   │   │                   #   ports/ (Reasoner/AgentRunner/Workspace/EventSink/Clock)
│   │   ├── app/            # Use cases + handlers (Execution/Gate/Planning),
│   │   │                   #   block_policy (what a block may advertise),
│   │   │                   #   agent_feedback / contract_repair /
│   │   │                   #   promotion_failures (bounded auto-recovery), ports.py
│   │   │                   #   (TaskFailed/Outbox/UnitOfWork/ChatStore + domain re-exports),
│   │   │                   #   execution/observation records, run_worker loop,
│   │   │                   #   conversation turns, transactional testing fakes
│   │   ├── infra/          # SQLite (UoW/plan repo/outbox/chat/reference/secrets), git
│   │   │                   #   workspace, CLI agent runners + taxonomy, reasoner/
│   │   │                   #   (stub + OpenAIReasoner + runtime/ tool loop + factory),
│   │   │                   #   worker entrypoint, container (composition root), CLI
│   │   └── api/            # FastAPI: thin routers, ONE error map, SSE broker,
│   │                       #   outbox relay, security, request logging
│   ├── tests/              # support.py + fakes_llm.py + unit/orchestration (dual-backend)
│   │                       #   + unit/reasoner + integration
│   ├── alembic/            # migration chain (0001_core through
│   │                       #   0015_plan_delete_cascade; one linear head)
│   └── docs/               # INTEGRATION_GUIDE.md — the frozen port contracts
├── docs/                   # system documentation:
│   ├── architecture/       #   overview, plan-lifecycle, execution-model, events,
│   │                       #   data-model, frontend, known-issues (verified defects),
│   │                       #   capability-matrix (what is supported + where it is
│   │                       #   exposed; route inventory is test-locked)
│   ├── decisions/          #   decision-log.md, ADRs, domain-design-decisions.md
│   ├── legacy/             #   pre-refactor features kept for reintroduction analysis
│   └── history/            #   archived plans/analyses/pre-refactor docs (immutable)
├── fixtures/               # operator walkthroughs (API-only: curl + jq, never the UI):
│                           #   happy-path-v1 (locked, Tier 0 + Tier 1),
│                           #   planning-recovery-v1 (a starved planning session
│                           #   leaves evidence the retry uses), parallel-goals-v1
│                           #   (two goals into one cycle branch), contract-repair-v1
│                           #   (Tier 1 only: poison a frozen contract, prove it is
│                           #   repaired in place — a run that must FAIL first).
│                           #   Never mix modes: Tier 0 = stub + dry-run,
│                           #   Tier 1 = llm + real.
├── ROADMAP.md              # everything planned but not yet implemented (+ do-not-do)
└── frontend/               # React/Vite on the thin API: plan list + /plans/:id shell,
                            #   chat panel, gates, 9-phase rail, goals canvas, SSE bridge
```

**Docs discipline**: a doc contradicting the code is a bug in the doc — fix it in the
same PR. Unimplemented ideas go to `ROADMAP.md`, never into `docs/architecture/`.
When fixing an entry in `docs/architecture/known-issues.md`, delete it and add the
regression test that locks it. Domain un-freezes get a `docs/decisions/decision-log.md`
entry.

## Temporary multi-runtime acceleration

- For an approved plan with multiple independent tasks, use `/accelerate <plan path or objective>`.
- Claude is the coordinator, not the default implementation worker.
- Runtime availability and quota live in `.orchestrator/runtime-pool.yaml`; do not duplicate them here.
- All delegated writing tasks require isolated Git worktrees, non-overlapping ownership, deterministic verification, and evidence before integration.
- Preserve domain boundaries: provider-specific runtime behavior belongs in infrastructure adapters, never the domain model.
- Do not change approved architecture during execution. Escalate ambiguity or decisions to the user.
