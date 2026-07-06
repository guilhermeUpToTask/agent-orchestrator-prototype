# Working Prototype — Real Reasoner (old architecture, better) + Frontend Re-point

## Context

The merge (branch `refactor/domain`) delivered the truth-tested core but left the planning LLM as a deterministic `StubReasoner` and the frontend broken against the new API. This plan makes the system a **working prototype**, per explicit direction:

- The reasoner is rebuilt on the **old planner runtime's architecture** (`git show 8f30306:backend/src/infra/runtime/planners/...`) — OpenAI-compatible client, **tool-calling agent loop** with terminal submit tools and `{accepted:false, errors}` self-correction, retry with transient/permanent classification, markdown context renderer — "identical but better", producing the new domain's goals/tasks.
- LLM credentials/model resolve **via the providers catalog** (SQLite providers/models + envelope-encrypted secrets + config keys), never env vars at runtime.
- DISCOVERY/REPLANNING become **real multi-turn chat with commit** (reasoner may ask/reply across turns; phase advances only when it submits the goal roadmap; history persisted per plan).
- **ENRICHING = the JIT successor, task population ONLY** (per correction): one goal per worker step, the reasoner breaks that goal into a small ordered set of plain executable tasks (with capability ids) — idempotent (goal with tasks = no-op), checkpointed goal-by-goal via `Signal.CONTINUE`. **No TDD test-writer/implementer pairing in this prototype.**
- **ARCHITECTURE phase — evaluated, kept as a passthrough**: the old system needed an autonomous architecture run because its discovery produced only a *brief*; here the discovery conversation commits the goal roadmap itself, so an LLM re-structuring pass is redundant (and risks mangling the user-agreed goal set). The phase stays in the frozen enum (REPLANNING→ARCHITECTURE flows through it; free crash checkpoint) but the handler is a **no-LLM passthrough**: validate + advance to ENRICHING. The seam is documented for a future real structuring pass.
- `src/domain/ports/` is **restored as first-class domain ports** (the merge deleted five 0-byte stubs; the real contracts existed in `src/app/ports.py` — they now move to domain with app re-exports).
- The **frontend is re-integrated**: regenerate OpenAPI types via the existing pipeline (`npm run generate:api` → `backend/scripts/export_openapi.py` + `openapi-ts`), rewrite the API/SSE/store layer, minimal working UI for the full 9-phase demo loop.
- The reasoner-layer **test coverage returns** (fake LLM client driving the loop, tool/parsing tests, conversation tests, JIT-enrich tests), plus a cost-gated real-model smoke test.

Every stage ends green: `ruff check src tests`, `mypy src` (zero errors, no excludes), `pytest`; from Stage 8 also `cd frontend && npm run build`. Each stage is one commit.

---

## Stage 1 — Restore `src/domain/ports/` (structural, zero behavior)

Create real Protocol files (contents moved verbatim from `src/app/ports.py`; domain-pure — pydantic/stdlib + domain types only):

| File | Contents |
|---|---|
| `src/domain/ports/__init__.py` | re-exports all |
| `src/domain/ports/reasoner_port.py` | `Reasoner` (current shape; redesigned Stage 3) |
| `src/domain/ports/agent_port.py` | `AgentRunner` |
| `src/domain/ports/workplace_port.py` | `WorkspaceHandle`, `Workspace` |
| `src/domain/ports/telemetry_port.py` | `AgentEventSink` |
| `src/domain/ports/planner_worker_port.py` | `Clock` |

`src/app/ports.py` keeps `TaskFailed`, `Outbox`, `UnitOfWork` and **re-exports** the five domain ports (`__all__`), so no call site churns (`conversation.py`, `planning_handler.py`, `run_worker.py`, `container.py`, fakes, tests). Verify: 208 tests green untouched, mypy strict clean (domain is fully strict).

## Stage 2 — Chat persistence substrate

- `src/infra/db/tables.py` + `alembic/versions/0003_chat.py`: `plan_chat_messages(id PK autoincrement, plan_id FK plans.id, role 'user'|'assistant', content TEXT, meta TEXT JSON default '{}', created_at)`, index `(plan_id, id)`.
- `src/domain/ports/reasoner_port.py`: `ChatMessage(BaseModel)` — role/content/created_at/meta.
- `src/app/ports.py`: `ChatStore(Protocol)` — `append(plan_id, message)`, `list(plan_id)`. Chat writes run **outside** the plan UoW (own short txns — a lost display reply never loses state).
- New `src/infra/db/chat_repository.py` (`SqliteChatRepository` via `run_in_session`); `InMemoryChatStore` in `src/app/testing/fakes.py`; container `chat_store` property.
- Tests: `tests/integration/test_chat_repository.py` (ordering, isolation, meta round-trip); extend `test_migrations.py`.

## Stage 3 — Conversational port + conversation rework + ARCHITECTURE passthrough + chat API

**The final port** (`src/domain/ports/reasoner_port.py`; `draft_goals`/`structure_goals`/`enrich_goals`/`replan_goals` all deleted — two methods total):

```python
ConversationMode = Literal["discovery", "replanning"]
class ReasonerReply(BaseModel):
    message: str
    goals: list[Goal] | None = None   # None = still conversing; set = commit the roadmap

class Reasoner(Protocol):
    async def converse(self, plan: Plan, history: Sequence[ChatMessage], message: str,
                       mode: ConversationMode) -> ReasonerReply: ...
    async def enrich_goal(self, plan: Plan, goal: Goal,
                          capabilities: Sequence[Capability]) -> list[Task]: ...
```

**`src/app/use_cases/conversation.py`**: both message use cases return `ConversationResult(reply, committed, phase)`. Flow: guard phase → persist the **user** message BEFORE the LLM call → `reasoner.converse(...)` outside any txn → if `goals is None`: append assistant reply (`meta committed=false`), phase unchanged; else re-open txn, re-guard, `set_iteration_goals`+`advance_phase(ARCHITECTURE)` (discovery) / `commit_replanned_goals` (replanning), bump, `PhaseAdvanced` outbox, save; append assistant reply (`meta committed=true`).

**`PlanningHandler._architect` becomes the passthrough** (no reasoner call): re-read in txn, re-guard phase, `advance_phase(ENRICHING)`, bump, `PhaseAdvanced`, save → `Signal.CONTINUE`. Docstring records the evaluation: discovery commits the roadmap, so autonomous structuring is redundant for the prototype; this is the seam if a real structuring pass returns.

**StubReasoner** tracks the new port: a message line `ask: <text>` → `ReasonerReply(text, goals=None)` (deterministic multi-turn hook); otherwise the existing `goal:/task:` grammar → commit. Dry-run keeps driving all 9 phases.

**API** (`src/api/routers/plans.py`): the two message endpoints now return `200 MessageResponse{reply, committed, phase}`; new `GET /plans/{id}/chat` → chat history (404 via `PLAN_NOT_FOUND`).

Tests: update all old-port call sites (grep); new multi-turn tests (ask-turn keeps phase + 2 chat rows; commit-turn advances), passthrough-architecture test, API body + history-order tests.

## Stage 4 — JIT ENRICHING: per-goal task population, checkpointed

`PlanningHandler(reasoner, agents, capabilities)` — `_enrich` is the JIT step, ONE goal per `handle()`:
1. target = first non-terminal goal (by position) with `tasks == []`;
2. if target: `tasks = await reasoner.enrich_goal(plan, target, capabilities.list())` (LLM outside txn) → re-open txn, re-guard phase, re-find goal by id — **if it now has tasks, no-op** (idempotency, the old JIT guard) — else rebuild the non-terminal goal list with the target's tasks set, `set_iteration_goals`, bump, save → `Signal.CONTINUE` (the between-goals crash checkpoint);
3. no target: existing path — `bind_agents` + `AgentFellBackToDefault` events → AWAITING_REVIEW → `PAUSED`.

Task semantics: **plain executable tasks** — 1..N per goal (small, ordered by `position`), each with name, description, and `required_capabilities` drawn from the catalog ids. Goals that already carry tasks (from chat grammar / `submit_goals` pre-seeding) are skipped entirely.

Stub `enrich_goal` → one deterministic task: `Task(name=f"implement: {goal.name}", position=0, description="[enriched] ...")`. Wire capability repo through `src/infra/worker/main.py` and test harnesses (`InMemoryCapabilityRepository` exists in fakes).

Tests: N task-less goals ⇒ N CONTINUEs then the binding PAUSED; idempotent re-entry (crash between LLM and commit / after commit); phase-race guard; full-cycle test gains a task-less goal asserting it got populated while grammar-specified tasks are untouched.

## Stage 5 — LLM runtime infra (the old architecture, ported)

New package `src/infra/reasoner/runtime/` (all async):
- `errors.py` — `ReasonerError(InfrastructureError, code=REASONER_FAILED, transient: bool)`; port `classify_provider_error` + empty-`choices` guard from the old `openai_adapter.py`.
- `tools.py` — `ToolSpec(name, description, input_schema, handler, terminal=False)` (the old `PlannerTool` shape) + `execute_tool_call` (unknown tool / handler exception → `{"error": ...}` result string).
- `llm_client.py` — `LLMClient(Protocol).complete(messages, tools) -> AssistantTurn(text, tool_calls, raw_message)`; `OpenAIChatClient` on **AsyncOpenAI** (`api_key, model, base_url, temperature=0.2, max_retries=3, sleep=asyncio.sleep`): ports `_request_message` retry (backoff `2.0**attempt`, transient-only), tolerant tool-arg `json.loads`→`{}`, old `{"type":"function",...}` tool wire shape.
- `agent_loop.py` — `run_tool_session(client, messages, tools, max_turns, allow_plain_reply) -> SessionResult(text, submitted)`: the old `BasePlannerRuntime` loop — terminal tool + parsed `{accepted:true}` ends; `{accepted:false, errors}` feeds back (self-correction); plain text ends the session when `allow_plain_reply` (conversational) else raises; budget exhaustion → `ReasonerError(transient=True)`.
- `context.py` — `render_plan_context(plan, *, include_results, max_result_chars=800)`: new-domain port of the old `PlanningContextRenderer` — brief, phase/iteration, goals with tasks/statuses, truncated `TaskResult.output` for DONE tasks (the REPLANNING context), prior-iteration terminal goals as one-liners; `render_capabilities(caps)`.
- `prompts.py` — system prompt + instruction blocks ported from `planning_prompt_builders.py`: `build_discovery_prompt`, `build_replanning_prompt`, `build_enrich_prompt(plan_ctx, goal, caps_md)` (no structure prompt — ARCHITECTURE is a passthrough).
- `tests/fakes_llm.py` — `FakeLLMClient(script)` popping scripted `AssistantTurn`s, recording calls; helpers `text_turn`/`tool_turn`.

Tests (`tests/unit/reasoner/`): loop terminal/self-correction/plain-reply/max-turns/malformed-args; retry + empty-choices classification; context renderer goldens.

## Stage 6 — OpenAIReasoner + catalog resolution + seed CLI

**`src/infra/reasoner/openai_reasoner.py`** — `OpenAIReasoner(client, capabilities, converse_max_turns=8, enrich_max_turns=4)`:
- `converse`: messages = system + history replayed as plain user/assistant **text** (never provider transcripts — immune to dangling-tool-call issues and provider switches) + phase prompt over `render_plan_context` (`include_results=True` for replanning). Tools: `submit_goals` (terminal). A text reply without submit IS the question → `ReasonerReply(text, None)`. No `ask_question` tool needed.
- `enrich_goal`: tool `submit_tasks` (terminal), `allow_plain_reply=False`, short budget; prompt includes the capability catalog markdown; unknown capability ids → `{accepted:false}` listing them (self-correct); after exhaustion filter unknown ids + structlog warning.

**Tool JSON schemas** (handlers re-validate everything — never trust provider schema enforcement — and build domain objects with `new_id()` and `position=index`):

```jsonc
// submit_goals — terminal for converse (both modes): the roadmap commit
{ "goals": [ { "name": str, "description": str,
               "tasks?": [ { "name": str, "description": str,
                             "required_capabilities?": [str] } ] } ] }   // minItems 1

// submit_tasks — terminal for enrich_goal: plain task population
{ "tasks": [ { "name": str, "description": str,
               "required_capabilities?": [str] } ] }   // minItems 1, small N (guide ≤6 in prompt)
```

**`src/infra/reasoner/factory.py`** — `build_reasoner(config_store, provider_repo, model_repo, secret_store_lazy, capability_repo)`. Config keys (scope `orchestrator`): `reasoner.mode` (`stub` default | `llm`), `reasoner.provider_id`, `reasoner.model_id`, `reasoner.temperature` (0.2), `reasoner.max_turns` (8). `llm` mode fail-fast (`InfrastructureError REASONER_CONFIG_INVALID` → add `422` to `_STATUS_BY_CODE`); key = `secret_store().resolve_plaintext(SecretRef(provider.api_key_ref))`; model string = `IAModel.name`; base_url from the provider row. **Stub mode never touches the secret store** (dry-run needs no master key). Container `reasoner` property → `build_reasoner(...)`.

**Seed CLI** — `orchestrate seed demo [--provider openai|openrouter|anthropic|gemini|local] [--model NAME] [--base-url URL] [--api-key-env VAR] [--stub]`: idempotent upserts — capabilities (backend/frontend/testing), agent `dev-agent` + `set_default`, provider row (preset base_urls ported from the old `_PRESETS`, key read once from the named env var → secret store), model row, `reasoner.*` config keys. `--stub` sets only `reasoner.mode=stub`.

Tests: factory (stub default, fail-fast messages, full resolution on seeded tmp DB), seed via `click.testing.CliRunner`, OpenAIReasoner behaviors on `FakeLLMClient` (ask vs commit; task population; unknown-cap correction).

## Stage 7 — Test rebuild + cost-gated smoke

- `tests/integration/test_full_cycle_llm.py`: the full 9-phase cycle + replan loop driven by `OpenAIReasoner(FakeLLMClient(script))` — scripted question turn, `submit_goals` (one goal task-less), per-goal `submit_tasks`, replanning `submit_goals`; asserts chat rows, phase walk (incl. the ARCHITECTURE passthrough), task positions, iteration 2. (`test_full_cycle.py` stays on the stub — the deterministic dry-run gate.)
- API chat tests in `test_api.py` (committed=false → true walk, `GET /chat` order).
- `tests/integration/test_reasoner_smoke.py`: marker `llm` (register in pyproject) + skipif `REASONER_SMOKE_API_KEY` unset; ONE real `converse` turn via `REASONER_SMOKE_BASE_URL`/`_MODEL`. Never runs in normal CI.

## Stage 8 — Frontend re-point

Order: types first, then bottom-up.
1. **Regen types**: `cd frontend && npm run generate:api` (pipeline exists: `backend/scripts/export_openapi.py` + `openapi-ts.config.ts`) → `src/types/generated/*`; fix `src/types/ui.ts` aliases to the new shapes (renames: `goal_id→id`, `task_id→id`, `title→name`, `assigned_agent_id→agent_id`, `retry_count→attempt`; statuses `pending|running|done|skipped|failed`; plan has `phase`+`iteration`, no `phases[]`).
2. **Rewrite `src/lib/api.ts`**: plan-scoped endpoints (POST `/api/plans` with `Idempotency-Key`, GET list/detail, edits/approve/review/finish/replan/messages returning `MessageResponse`, `GET /chat`), reference CRUD (`/api/agents|capabilities|providers|models|config`), SSE `GET /api/events`. Delete sessions-polling, refine, discovery-session, forge.
3. **Rewrite `src/lib/queries.ts`**: keys `['plans']`, `['plan',id]`, `['chat',id]`; new mutations; `useSSEBridge` switch on the new vocabulary with `plan_id` filtering — `PhaseAdvanced` (invalidate + gate toasts), `TaskStarted/TaskCompleted/TaskRequeued/TaskFailedEvent/TaskAbandoned/GoalCompleted/GoalFailedEvent` (invalidate + activity feed), `PlanCompleted/PlanFailed/ReplanRequested/AgentFellBackToDefault` (toast), `agent.event` (ConsoleDock live log; dedup on `event_id`).
4. **Gut `src/store/plannerStore.ts`**: drop decisions/phases/activeRun/completedRuns machinery; keep chat/events/ui; add `currentPlanId`.
5. **Components**: `ChatPanel` (enabled in discovery/replanning; send → POST → append reply; hydrate from `['chat',id]`), `GatePanel`+`LifecycleRail` (two gates: awaiting_review→approve, review→finish/replan; 9-phase stepper), `PhaseTimeline` (9 phases), `TaskNode/GoalGroupNode/StatusBadge/TopBar/Overview/Goals/Activity` (rename/status sweep), `layout.ts` (keep dagre two-level; group by goal; drop phases array).
6. **New**: route `/plans/:id`, plan list/picker view (GET summaries), "New plan" dialog.
7. **Delete**: PullRequests view + forge api/queries/types, plan-history UI, secrets UI, session machinery; `ConsoleDock` rewired to `agent.event` (hide if time-boxed); `Settings` re-pointed to the reference + config endpoints.
8. Verify: `npm run build`; manual demo (below).

## Stage 9 — Docs + final sweep

Update `backend/docs/INTEGRATION_GUIDE.md` (new two-method reasoner port + tooling, ARCHITECTURE-passthrough rationale, chat endpoints/table, config keys, seed command), root `CLAUDE.md`, container docstring. Full gates.

---

## Verification (end-to-end)

1. Per stage: `ruff check src tests && mypy src && pytest` (backend), plus targeted new tests named above.
2. Stub demo (no key needed): `orchestrate db upgrade && orchestrate seed demo --stub`, start API + worker (`AGENT_MODE=dry-run`), drive a plan in the **UI**: create → chat (`ask:` roundtrip then goal grammar commit) → ARCHITECTURE flashes through (passthrough) → ENRICHING populates tasks goal-by-goal → approve → live execution → review → replan chat → iteration 2 → finish.
3. Real-LLM demo: `orchestrate seed demo --provider openrouter --model <model> --api-key-env OPENROUTER_API_KEY` (or openai/anthropic/gemini preset) → same UI flow with genuine planning; `pytest -m llm` with `REASONER_SMOKE_*` set for the smoke test.

## Key risks

- **Async**: AsyncOpenAI + injected `asyncio.sleep` (the sync SDK would block the worker loop).
- **Provider schema laxity**: handlers re-validate all tool args; `{accepted:false}` self-correction; budget exhaustion is a transient `ReasonerError` → worker logs, backs off, retries after lease (the existing worker guard handles this).
- **Context growth**: truncate results (~800 chars), cap history (~30 messages), summarize prior-iteration terminal goals to one-liners.
- **Chat reply transport**: HTTP response body (ordered, simple); SSE carries only the domain events — no dual-publish.
- **Dry-run stays deterministic**: stub default mode, stub implements the full new port, factory never touches the secret store in stub mode.
