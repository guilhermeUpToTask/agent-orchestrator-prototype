# INTEGRATION_GUIDE — the frozen contracts

The per-port contract handbook the integration was executed against (roadmap
Phases 0–2 + slices of 3/4). If the roadmap and this guide ever disagree, fix
this guide first, then implement. Updated at the end of the integration
(2026-07-03); the domain is FROZEN — these contracts change only with a
deliberate un-freeze.

## The phase machine (domain, frozen)

`PlanPhase`: DISCOVERY, REPLANNING, ARCHITECTURE, ENRICHING, AWAITING_REVIEW,
RUNNING, REVIEW, DONE, FAILED. Terminal = {DONE, FAILED}.

Driver model — who advances each phase:

| Phase | Driver | Worker-claimable |
|---|---|---|
| DISCOVERY, REPLANNING | chat/API (`conversation.py` use cases) | NO |
| ARCHITECTURE, ENRICHING | worker (`PlanningHandler`) | YES |
| RUNNING | worker (`ExecutionHandler`, the pull-scan) | YES |
| AWAITING_REVIEW, REVIEW | human command (`control.py`) | NO |

Gates ALWAYS pause. RUNNING exhausts into REVIEW; DONE is reached ONLY via
`finish_review()` (which emits `PlanCompleted`). The replan loop:
`begin_replanning()` (from REVIEW or mid-RUNNING) skips PENDING work now;
`commit_replanned_goals()` finalize-abandons whatever remained non-terminal,
appends the new goals, increments `iteration`, and flows into ARCHITECTURE.
Late in-flight results are handled by the TOLERANT FINALIZE in
`ExecutionHandler` (late failure terminal-skips, never requeues; late success
completes as history unless the task was already closed).

**ARCHITECTURE is a no-LLM passthrough** (`PlanningHandler._architect`): the
discovery/replanning conversation commits the user-agreed roadmap itself, so an
autonomous re-structuring pass would be redundant — and risks mangling a goal
set the user just signed off. The phase stays in the frozen enum (REPLANNING
re-enters through it; free crash checkpoint); the handler validates + advances
to ENRICHING and is the seam if a real structuring pass returns.

**ENRICHING is the JIT step** (`PlanningHandler._enrich`): ONE task-less goal
per `handle()` — `reasoner.enrich_goal(plan, goal, capabilities)` outside the
txn, then re-read/re-guard/re-find-by-id (a goal that already has tasks is
NEVER re-enriched — the idempotency guard for crashes and lease races), commit
via `set_iteration_goals`, `Signal.CONTINUE`. Goals the user populated in chat
are skipped entirely. No task-less goal left ⇒ `bind_agents` → AWAITING_REVIEW.

## Reasoner (the planning LLM)

The domain port (`src/domain/ports/reasoner_port.py`) is exactly two methods:

```python
converse(plan, history, message, mode) -> ReasonerReply  # message + goals|None
enrich_goal(plan, goal, capabilities) -> list[Task]
```

`converse` is MULTI-TURN WITH COMMIT: goals=None keeps the conversation open
(the reply is a question — chat rows persist with `meta.committed=false`); a
goal list is the roadmap commit. The conversation use cases persist the USER
message BEFORE the LLM call (it survives reasoner crashes), call the reasoner
outside any txn, and re-guard the phase before writing.

Implementations behind the same port:

* `StubReasoner` — deterministic. `ask: <text>` in a message ⇒ reply without
  commit; otherwise the `goal:/task: [caps: a,b]` grammar ⇒ commit (goals may
  be committed task-less — the JIT populates them). Dry-run/tests run on this.
* `OpenAIReasoner` on `src/infra/reasoner/runtime/` — the tool-calling agent
  loop (`run_tool_session`): terminal submit tools (`submit_goals`,
  `submit_tasks`), `{accepted:false, errors}` self-correction, AsyncOpenAI
  with transient/permanent retry classification + the in-band empty-choices
  guard, plan→markdown context renderer (results truncated ~800 chars, history
  capped at 30 messages, terminal goals as one-liners). Handlers RE-VALIDATE
  every tool argument; unknown capability ids are rejected twice then filtered
  with a warning. History replays as plain text, never provider transcripts.

Selection is catalog-driven (`src/infra/reasoner/factory.py`), config scope
`orchestrator`: `reasoner.mode` (`stub` default | `llm`), `reasoner.provider_id`,
`reasoner.model_id`, `reasoner.temperature`, `reasoner.max_turns`. `llm` mode
fail-fasts with `REASONER_CONFIG_INVALID` (HTTP 422); the API key resolves
through the provider row's `api_key_ref` into the envelope-encrypted secret
store. STUB MODE NEVER CONSTRUCTS THE SECRET STORE (dry-run needs no master
key). `orchestrate seed demo [--stub | --provider … --model … --api-key-env …]`
idempotently seeds capabilities, the default agent, provider/model rows and
the config keys.

## Chat persistence

`plan_chat_messages` (migration 0003): append-only per-plan history, written
on its OWN short transactions (`SqliteChatRepository`) — never inside the plan
UoW. A lost display reply never loses plan state; a state rollback never
erases what the user said. The `ChatStore` port lives in `src/app/ports.py`;
the in-memory mirror is `InMemoryChatStore`.

## PlanRepository (SQLite) — the exact shapes

Aggregate = ONE JSON document in `plans.data`; promoted columns only for SQL
predicates. Every `get()` parses fresh JSON → `PlanFactory.reconstruct`
(detached aggregates, like the in-memory fake's deep copies).

**Version CAS** (use cases `bump_version()` BEFORE `save()`; store rejects
`stored.version >= incoming.version`):

```sql
INSERT INTO plans (id, version, phase, iteration, data, created_at, updated_at)
VALUES (...)
ON CONFLICT(id) DO UPDATE SET version=excluded.version, phase=excluded.phase,
    iteration=excluded.iteration, data=excluded.data, updated_at=excluded.updated_at
WHERE plans.version < excluded.version
-- rowcount 0 -> StaleVersionError. save() NEVER touches lease columns.
```

**Claim** (single atomic statement; `:now` = epoch seconds from the injected
Clock — FakeClock drives lease expiry in tests):

```sql
UPDATE plans SET claimed_by=:w, claimed_at=:now, lease_expires_at=:now+:lease,
                 lease_seconds=:lease
WHERE id = (SELECT id FROM plans
            WHERE phase IN ('architecture','enriching','running')
              AND (claimed_by IS NULL OR lease_expires_at < :now)
            ORDER BY updated_at LIMIT 1)
RETURNING data
```

`heartbeat`/`release` run on their OWN short transactions — `drive_plan` calls
them OUTSIDE the UnitOfWork blocks. Heartbeats happen between units, never
mid-agent-run, so `lease_seconds` must exceed the longest single task run.

## UnitOfWork (SQLite)

RE-ENTERABLE: fresh Session + transaction per `with` block; commit persists
state + outbox atomically, rollback discards both (the open txn IS the outbox
staging area). One instance per worker/request — not thread-safe.

## AgentRunner

```python
async def run(task, spec, *, idempotency_key, event_sink, workspace) -> TaskResult
```

Raises `TaskFailed(reason, kind)` with the SHARED FAILURE TAXONOMY
(`FailureKind`): connection_error / rate_limit / timeout / tool_error are
retryable; **token_limit / auth_error are terminal**
(`RetryPolicy.non_retryable_kinds`). The classifier
(`src/infra/runtime/taxonomy.py`) is conservative: unknown output ⇒ TOOL_ERROR
(retryable). The dummy runner (`AGENT_MODE=dry-run`) emits the same kinds, so
dry-run tests exercise exactly the production retry/terminal paths. The pi
NDJSON streaming contract is isolated in `src/infra/runtime/pi_protocol.py`
(seam, not yet implemented).

## Workspace (git branching = the rollback)

`begin(plan_id, task_id, attempt)` → branch `task/<task_id>/a<attempt>` off
`plan/<plan_id>` (created off main on first use) + worktree.
`commit(handle)` → commit the worktree, `--no-ff` merge into the plan branch.
`discard(handle)` → remove worktree + delete branch: zero trace, retries begin
clean from the plan branch (stateless task execution).

## Worker entrypoint

`worker_tick` returns PROGRESS, not claiming (a claim that yields only
not_ready/paused with zero steps returns False → caller sleeps — the spin
fix). `run_worker_forever` = tick; sleep poll_seconds only on no-progress; a
tick exception is logged and never kills the worker. Crash recovery = lease
expiry + any worker's next claim.

## Outbox relay (events become visible here)

Poller thread in the API process: `SELECT ... WHERE delivered_at IS NULL ORDER
BY id` → `broker.publish` → mark delivered. Publish-then-mark = AT-LEAST-ONCE;
every payload carries `event_id` — consumers dedup on it. The same thread
tails `agent_events` by cursor → `"agent.event"`.

## API → use-case mapping

| Route | Use case |
|---|---|
| POST /api/plans (Idempotency-Key) | create_plan |
| POST /api/plans/{id}/discovery/message → 200 MessageResponse | conversation.discovery_message |
| POST /api/plans/{id}/replanning/message → 200 MessageResponse | conversation.replanning_message |
| GET /api/plans/{id}/chat | ChatStore.list (404 via PLAN_NOT_FOUND) |
| POST /api/plans/{id}/edits | apply_edit (incl. RebindTaskAgent) |
| POST /api/plans/{id}/approve | control.resume_from_review |
| POST /api/plans/{id}/review/finish | control.finish_review |
| POST /api/plans/{id}/review/replan | control.review_replan |
| POST /api/plans/{id}/replan | request_replan (mid-RUNNING) |
| /api/agents, /capabilities, /providers, /models, /projects | reference repos |
| /api/config/{scope}[/{key}] | SqliteConfigStore (two-tier) |
| GET /api/events | SSE stream (relay-fed, NAMED events) |

`MessageResponse` = `{reply, committed, phase}` — committed=false is a question
turn (phase unchanged), committed=true is the roadmap commit. The chat reply
travels in the HTTP response body; SSE carries only the domain events.

Error mapping lives in ONE table: `src/api/exceptions.py::_STATUS_BY_CODE`.

## Verification procedure (the truth test)

`pytest -m integration` runs the in-memory orchestration suite AGAINST THE
REAL SQLite UnitOfWork via the parametrized `env_factory` fixture
(`tests/unit/orchestration/conftest.py` + `tests/support.py`). The tests that
prove atomicity is real, not simulated: crash-recovery-via-lease-expiry,
outbox-rollback, backoff-gate-survives-crash. `tests/integration/
test_full_cycle.py` drives all nine phases + one replan loop on the stub;
`test_full_cycle_llm.py` drives the same walk through OpenAIReasoner on a
scripted FakeLLMClient (`tests/fakes_llm.py`); `tests/unit/reasoner/` covers
the agent loop, retry classification, context renderer and OpenAIReasoner
behaviors. The cost-gated real-provider smoke is `pytest -m llm` with
`REASONER_SMOKE_API_KEY` (+ `_BASE_URL`, `_MODEL`) — never in normal CI.

The manual check: `orchestrate db upgrade && orchestrate seed demo --stub`,
`orchestrate api start` + `orchestrate worker start` (AGENT_MODE=dry-run),
then drive a plan DISCOVERY→DONE (+ one replan loop) in the UI or over HTTP —
an `ask:` message keeps the conversation open, a `goal:/task:` message commits.

## Deferred (cleanly shelved — the seams)

GitHub PR output (workspace port), project spec, decision gate, Redis claim
path (roadmap Phase 3 — the SQLite lease is the current transport), a real
autonomous ARCHITECTURE structuring pass (the handler passthrough is the
seam), pi NDJSON streaming (pi_protocol.py), mutation guards
PLAN_BUSY/TASK_RUNNING (status codes already reserved in the API map), env
provisioner. Deleted-but-recoverable via git history: Redis adapters,
LiveLogger, SettingsService, the old PR/forge stack.
