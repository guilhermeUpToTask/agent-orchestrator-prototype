# Remediation Plan: Code Review Findings (docs/code-review-report.md)

## Context

The full-codebase review (2026-06-11, `docs/code-review-report.md`) found the hexagonal core sound but identified critical defects at the edges, all **independently verified against the working tree**:

- `task.assigned` is delivered to a random worker via a shared consumer group; wrong workers ACK and drop assignments (`src/infra/cli/system/commands.py:222-233`).
- `YamlTaskRepository` CAS is documented single-process-only, yet 4+ processes write through it.
- No Redis→SSE bridge exists; `publish_sse` is called only from API routers + the planner hook (verified by grep), so the frontend never sees worker/reconciler/goal-orchestrator progress. SSE uses one global queue (`src/api/sse.py:17`) — multiple tabs steal events; `put_nowait` is called from threadpool threads (unsafe).
- `system start` never starts the goal orchestrator; the PR-capable variant (`task_graph_orchestrator_with_pr`) is dead code — tasks are never merged, PRs never opened.
- No `XPENDING`/`XAUTOCLAIM` recovery anywhere — crashed consumers lose events permanently.
- Discovery router: module globals, `_discovery_active` never reset on failure (permanent 409), timeout-as-protocol, unreachable final brief.
- Build health confirmed: **14 failed / 1206 passed**, **66 ruff errors**, 419 mypy errors under `strict = true`.

**User decisions:** full scope (all milestones, phased so work can stop at any boundary); mypy via honest per-layer config with a ratchet, not full-strict everywhere.

**Architecture decisions (per the report, not re-litigated):** no RQ; embed the three coordinators (task-manager, goal orchestrator, reconciler) as lifespan threads in the FastAPI process; keep workers as separate supervised processes; achieve single-writer task state by making workers publish result events instead of CAS-writing (no storage-engine swap).

---

## M1 — Delivery correctness + trustworthy baseline (~2-3 days, three parallel tracks)

### 1.1 Per-agent consumer groups for `task.assigned` (Critical, S)
Use **per-agent groups on the shared stream** (not per-agent streams): `TaskGraphOrchestrator` (`src/app/orchestrator.py:60-68`) also subscribes to `task.assigned` (group `goal-orchestrator`) to transition goals PENDING→RUNNING; per-agent streams would break that or force dual-publish.
- `src/infra/cli/system/commands.py:222-233`: `group="workers"` → `group=f"worker-{agent_id}"` (both subscribe and ack). The skip-not-mine branch stays, now harmless (downgrade log to debug).
- No publisher or adapter changes.
- Migration note: stale `workers` group PEL — run/document `XGROUP DESTROY events:task.assigned workers`.
- **Test:** fakeredis integration (pattern: `tests/integration/test_e2e_full.py`): two worker groups each see all assignments; only the addressee processes.

### 1.2 PEL recovery (High, S)
In `RedisEventAdapter.subscribe_many` (`src/infra/redis_adapters/event_adapter.py:47-101`):
- After `xgroup_create`, a recovery phase: `xreadgroup(..., {stream: "0"})` loop until empty, yielding through the existing decode path (replays own un-acked messages).
- In the main `">"` loop, time-based periodic `XAUTOCLAIM(min_idle_time=claim_idle_ms, start="0-0")`; `claim_idle_ms` constructor param, default 300_000. For worker usage, idle threshold must exceed `task_timeout` (or disable XAUTOCLAIM; startup replay is the load-bearing fix since all groups are single-consumer after 1.1).
- **Test:** extend `tests/unit/infra/redis_adapters/test_event_adapter.py` (fakeredis): unacked message redelivered to fresh adapter same group/consumer; acked not replayed; XAUTOCLAIM claims from a dead consumer with `claim_idle_ms=0`.

### 1.3 Dead code + packaging (Medium, S) — clears 10 of 14 failing tests
- **Delete:** `src/infra/github/github_client.py` (377 LOC, unimported); `src/cli.py` and `src/dependency_checker.py` shims; `RunPlanningSessionUseCase` + `tests/regression/app/usecases/test_run_planning_session.py` (the 10 failures) + its `__init__.py` export; the `plan decision` stub command (`src/infra/cli/plan/commands.py:437-451`).
- **Repoint imports:** `tests/unit/infra/test_cli.py:6`, `test_cli_new_commands.py:6` → `src.infra.cli.main`; `test_wizard.py:14`, `test_dependency_checker.py:10` → `src.infra.dependency_checker`.
- **Container** (`src/infra/container.py:596-628`): make `task_graph_orchestrator` pass `create_pr_usecase=self.create_goal_pr_usecase` (dry-run safe — `StubGitHubClient`); **delete** `task_graph_orchestrator_with_pr`. Fix the phantom `PlannerRuntimePort` annotation (~line 262).
- **pyproject.toml:** move dev tools (pytest, pytest-asyncio, pytest-cov, mypy, ruff, fakeredis, coverage + transitive pins) from runtime `dependencies` to `[dev]`; drop unused `rq`, `prometheus-client`, `tenacity`, `python-ulid`; add `[project.scripts] orchestrate = "src.infra.cli.main:cli"`. Naming sweep: standardize on `orchestrate` in help/error text. Fix CLAUDE.md repo-layout drift.

### 1.4 Green tests + ruff (Medium)
- Remaining 4 failures (`test_planner_orchestrator.py` hook tests ×3, `test_plan_goal_tasks_hooks.py` ×1) share one cause: a MagicMock leaking into `src/app/services/planner_context.py:189` prompt assembly — fix the test fixtures/seam, not prod code.
- `ruff check --fix` (21 auto) + hand-fix remaining ~45.
- Add a `make check` (or CI gate) so M2+ lands on green.

### 1.5 Hotfix: discovery 409 leak (5 lines)
Reset `_discovery_active = False` in a `finally` around the executor call in `src/api/routers/discovery.py` — cherry-picked now even though §7 rewrites the router in M4.

---

## M2 — Topology: embed coordinators in the API; `system start` supervises only workers (~2-3 days)

### 2.0 Spec staleness first (Medium, S)
Embedding makes container staleness worse, so fix it first. `src/infra/container.py:527-561, 660-671`: demote `current_spec` from `cached_property` to plain `property` (spec load = one file read); make `validate_against_spec_usecase`, `planner_context_assembler`, `sync_goal_pr_usecase` resolve the spec per call. Replace `sync_goal_pr_usecase`'s `except Exception: pass` with a specific catch + logged warning.

### 2.1 Make the loops embeddable (stop semantics)
- `EventPort.subscribe_many/subscribe` (`src/domain/ports/messaging.py`): optional `stop: Callable[[], bool] | None = None` (default keeps all callers valid).
- `RedisEventAdapter`: check `stop()` each iteration; lower `block` 5000→1000.
- `InMemoryEventAdapter`: add per-group cursors so re-subscribe yields only new events (preserves finite-generator semantics for existing tests — verified consumers use a fresh default group once per test). This also un-breaks the documented `AGENT_MODE=dry-run system start`.
- `Reconciler.run_forever` (`src/app/reconciliation/reconciliation_engine.py:98-103`): `threading.Event`-based loop, **wait first** (gives a boot grace period before failing not-yet-heartbeating agents); add `shutdown()`.
- `TaskGraphOrchestrator` (`src/app/orchestrator.py:154-180, 396-403`): **`signal.signal` raises `ValueError` off the main thread** — add `install_signal_handlers: bool = True` param; pass `stop=lambda: not self._running` into `subscribe_many` so `shutdown()` actually unblocks the loop.

### 2.2 Extract runner functions
New `src/app/runners.py` (app layer, ports only): `run_task_manager_loop(handler, events, stop)` (lifted from `commands.py:149-189`, with resubscribe outer loop), `run_goal_orchestrator_loop`, `run_reconciler_loop`. CLI commands `system task-manager` / `system reconciler` / `goals run` become thin wrappers (standalone escape hatch preserved).

### 2.3 Lifespan wiring (`src/api/server.py`)
`asynccontextmanager` lifespan on `create_app`: resolve one container at startup, **touch needed cached properties on the startup thread before spawning threads** (cached_property is unlocked — avoids race-built duplicates), then start three daemon threads running the runners (+ the SSE bridge slot for M4). Shutdown: set the shared Event, call `shutdown()`s, join with timeout. Kill-switch env (`ORCHESTRATOR_EMBED_COORDINATORS=0`) keeps existing API tests thread-free. Coordinators bind the boot-time container; log a prominent warning on `DynamicContainerProvider` fingerprint change that a restart is needed. This closes §3.4 (goal orchestrator now always runs, PR-enabled per 1.3).

### 2.4 Slim `system start` + real supervision (`src/infra/cli/system/commands.py:36-147`)
Remove task-manager/reconciler spawns. Supervisor for **workers only**: warn once per crash, restart with exponential backoff (2s·2ⁿ cap 60s, reset after healthy period), give up after ~5 fast crashes; API exit terminates `system start`. Thread reconciler interval/stuck-age flags through to the API process.

**Tests:** runner units with `InMemoryEventAdapter` + stop flag; supervisor restart/backoff in `tests/integration/test_system_start_lifecycle.py`; lifespan start/stop via `TestClient` + fakeredis container; `test_e2e_dry_run.py` stays green.

---

## M3 — Single-writer task state (Critical, ~2 days; requires M1.1 + M2)

Workers stop CAS-writing; they publish execution-result events; the embedded task-manager is the sole task-state writer.

- **New event types** (payloads carry immutable result facts — amend the "IDs only" note in `src/domain/ports/messaging.py:20`): `task.execution_started {task_id, agent_id}`, `task.execution_succeeded {task_id, agent_id, branch, commit_sha, modified_files, artifacts}`, `task.execution_failed {task_id, agent_id, reason}`.
- **`src/app/usecases/task_execute.py`**: replace `_start_task_with_retry` / `_persist_success` / `_persist_failure` bodies with event publishes; keep validation, workspace, session, commit, lease logic. Worker keeps read access.
- **New `src/app/usecases/task_record_result.py`** — `TaskRecordResultUseCase`: the three CAS transitions moved here (with idempotency guards: not-ASSIGNED → skip on redelivery). After applying, publish the canonical `task.started/completed/failed` so downstream consumers (`TaskUnblockUseCase`, `GoalMergeTaskUseCase`) are untouched; on failure, call `TaskFailHandlingUseCase` directly (no self-roundtrip), keeping `task.failed` as pure notification.
- **`src/app/handlers/task_manager.py`** + runner/CLI dispatch: add the three handlers; result handlers need the full event (payload), not just `task_id` — adjust dispatch in `runners.py` / `commands.py:164-184`.
- **`src/infra/fs/task_repository.py`**: add a `threading.Lock` around read-compare-write in `update_if_version`/`save` (single process, many threads now); rewrite the §3.2 docstring to the new contract: "safe for one process, many threads; workers must not write".
- **`src/infra/container.py`**: wire `TaskRecordResultUseCase`.
- Crash semantics: worker dies after publish → PEL recovery (1.2) redelivers to TM; TM down → events queue in the stream.

**Tests:** spy-repo assertion that `TaskExecuteUseCase` performs zero writes; `TaskRecordResultUseCase` transition matrix incl. idempotency + CAS retry; fakeredis e2e: assign → execute → result event → TM writes SUCCEEDED → `task.completed` observed; update tests asserting direct worker writes.

---

## M4 — SSE bridge + session-style endpoints (~3-4 days; item 4.1 parallelizable with M2/M3)

### 4.1 Redis→SSE bridge, per-client fan-out, thread-safe publish (High, M)
- **Rewrite `src/api/sse.py`** as an `SSEBroker`: per-client `asyncio.Queue` registry (`register()/unregister()`, maxsize 200, drop-with-warn per client); thread-safe `publish` via `loop.call_soon_threadsafe` when off-loop (fixes the threadpool `put_nowait` violation). Keep the module-level `publish_sse()` shim so existing call sites (`server.py:40`, `routers/{plan,goals,refinement}.py`) keep working.
- **New `src/api/event_bridge.py`**: lifespan thread doing plain `XREAD` on `events:all` from `"$"` (no group, no ack — every API instance sees everything), `block=1000`, stop-flag + reconnect backoff. Explicit mapping table: `task.created/assigned/started/completed/failed/requeued/canceled` → `task.status_changed {task_id, status}` (the frontend handler at `frontend/src/lib/queries.ts:261` comes alive); `goal.*` → existing invalidation vocabulary + pass-through `goal.pr_opened`; `plan.*` pass through; unknown → forward as-is.
- **`src/api/routers/events.py`**: per-connection register/unregister in `finally`; keep ping + retry.
- **Frontend** (`frontend/src/lib/queries.ts`, `frontend/src/lib/api.ts`): reconnect resync (on `open` after `error` → `qc.invalidateQueries()`), a `default:` invalidation branch, extend the `SSEEvent` union.
- **Tests:** broker fan-out to two queues; off-loop publish (pytest-asyncio); pure-function mapping-table test; fakeredis integration: domain publish → bridge iteration → SSE queue.

### 4.2 Discovery/refine → 202 + session_id + SSE progress (Medium, M; requires 4.1)
- New `src/api/sessions.py`: locked `SessionRegistry` keyed by session_id — `status (running|waiting_input|done|failed)`, `current_question`, `result`, `answer_q` with `get(timeout=1800)` (frees abandoned threadpool threads), TTL GC. Completion via an **explicit sentinel** pushed in a `finally` wrapper around `start_discovery` — no more timeout-as-protocol.
- **Rewrite `src/api/routers/discovery.py`**: `POST /plan/discovery/start` → `202 {session_id}`; questions published as `plan.discovery_question` SSE *and* stored in registry; `POST .../{session_id}/message` → `202`; `GET /plan/discovery/{session_id}` → `{status, question?, brief?}` (fixes the unreachable final brief). Delete the module globals.
- **`src/api/routers/refinement.py`**: `POST /plan/refine` → `202 {session_id}`, use case runs on the executor, progress via existing `plan.refinement_action` SSE; `GET /plan/sessions/{session_id}` for the terminal result.
- **Schemas + codegen:** new `SessionAccepted`/`SessionStatus` models; regenerate OpenAPI (`scripts/export_openapi.py`) and frontend `types.gen.ts`; update `sendChatMessage`/`isThinking` flow to consume 202 + SSE with GET-poll fallback.
- **Tests:** stub-orchestrator API integration: start→202, question via GET, completion→brief retrievable, failure→new start succeeds (409-leak regression), answer timeout→session failed.

---

## M5 — CLI fixes + mypy ratchet (~2-3 days, fully parallelizable)

### 5.1 CLI fixes (`src/infra/cli/plan/commands.py`, `main.py`, `goals/commands.py`)
- **Architect edit flow** (lines 274-283): replace tempfile + `os.system($EDITOR)` + unlink-without-readback with `click.edit(decision.content)`; apply the edit to the pending decision, re-prompt, append to `approved_ids`. Kills shell injection + silent edit loss.
- **Spinner/prompt collision:** no `_spinner` around interactive sessions — static status line instead.
- **Container-once:** `ctx.obj = LazyContainer()` (lazy so `init`/wizard run without config) in the `cli` group callback; commands use `@click.pass_obj`; delete `_require_project()`'s throwaway container.
- **Mode without env mutation:** `AppContainer.from_env(mode=...)` → `SettingsService.load(mode_override=...)`; delete the `os.environ["AGENT_MODE"]` writes.
- **`planner_session` context manager** extracting the 3× repeated logger/hook/session choreography (lines ~148-164, 214-230, 329-344).
- **`plan status`:** read JIT enablement from config (not hardcoded); replace `except Exception: pass` with logged warning.
- **`plan review` prompt matrix:** no/no path prints "No action taken" instead of silence.
- **`LiveLogger.replay(events)`** public method; `plan logs` stops calling `_render_to_terminal`.
- Remove duplicate `AppContainer` import, unused locals.
- **Tests:** `CliRunner` — edit flow with `click.edit` monkeypatched; review prompt matrix; `--dry-run` leaves `os.environ` untouched.

### 5.2 Mypy: honest per-layer config (decided with user)
1. Fix outright bugs mypy found (phantom `PlannerRuntimePort` — done in 1.3).
2. True strict for `src/domain` + `src/app`.
3. `[tool.mypy.overrides]` relaxations for `src/infra/cli` and `src/api/routers` with tracked TODOs.
4. Ratchet: CI runs mypy, fails on regression. Timebox ~2 days; goal = zero errors under the declared config, and update CLAUDE.md's "strictly typed" claim to match reality.

---

## Verification (end of each milestone)

- `pytest` green (M1 onward: 0 failures), `ruff check src tests` clean (M1 onward), `mypy src` clean under declared config (M5).
- **M1:** fakeredis test proving every worker group sees every `task.assigned`; kill-a-consumer test proving PEL replay.
- **M2:** `AGENT_MODE=dry-run python -m src.infra.cli.main system start` — all daemons stay alive, no warn-spam, Ctrl-C clean shutdown; kill a worker → restarted with backoff, warned once.
- **M3:** dry-run e2e — task assigned → executed → TM (not worker) persists SUCCEEDED with intact history; grep confirms `TaskExecuteUseCase` has no `update_if_version` calls.
- **M4:** two browser tabs on the dashboard both receive every `task.status_changed` during a dry-run; discovery flow completes through the session endpoints and the final brief is retrievable; killing the API mid-discovery doesn't brick subsequent starts.
- **M5:** `uv run orchestrate --help` works; `plan architect` edit round-trips; CI gate (`make check`) enforces pytest + ruff + mypy ratchet.

## Sequencing summary

M1 (3 parallel tracks: events / dead-code / tests) → M2 (spec staleness first, then topology) → M3. M4.1 can run in parallel with M2/M3 (coordinate `server.py` lifespan scaffold); M4.2 after M4.1. M5 anytime after M1. Stoppable at any milestone boundary.
