# AIPOM / Agent Orchestrator — Full Codebase Review

**Date:** 2026-06-11
**Scope:** Backend (~24.5k LOC Python), Frontend (~4.3k LOC TS/React), CLI, process architecture
**Branch reviewed:** `feat/backlog-api-stability`

---

## 1. Executive Summary

The codebase has a genuinely clean hexagonal core: the dependency rule is respected (verified — zero `app`/`infra`/`api` imports inside `src/domain`, zero `infra` imports inside `src/app`), aggregates own their transitions, writes go through CAS with retry, and file writes are atomic with fsync + quarantine. That discipline is real and worth preserving.

The problems are concentrated at the **edges**: process topology, event delivery semantics, and the API↔worker↔frontend seams. The most important findings, in order of severity:

1. **Task assignment is delivered by roulette.** All workers share one Redis consumer group on `task.assigned`, so each event reaches exactly *one* worker — and if it's the wrong one, it ACKs and drops the event. With N workers, an assignment reaches the right worker with ~1/N probability on first delivery (§3.1).
2. **The storage layer forbids the process model the system actually runs.** `YamlTaskRepository` documents that its CAS is "ONLY safe if exactly one orchestrator process runs", yet `system start` boots 4+ concurrent writer processes (§3.2).
3. **The frontend's live-update channel is mostly fiction.** Workers/reconciler/goal-orchestrator publish to Redis; nothing bridges Redis → SSE. The frontend handles `task.status_changed` events that the backend never emits. SSE also uses a single shared queue, so two open browser tabs steal events from each other (§3.3, §5).
4. **`system start` never starts the goal orchestrator**, and the variant that *can* open PRs (`task_graph_orchestrator_with_pr`) is dead code — so out of the box, completed tasks are never merged and PRs are never opened (§3.4).
5. **Crashed consumers lose events permanently.** No `XPENDING`/`XAUTOCLAIM` recovery exists anywhere; the reconciler partially masks this (§3.5).

On the architecture questions asked: **don't adopt RQ** (it duplicates machinery you already have and fixes none of the actual problems); **do fold the three coordinator daemons into the FastAPI process**; **keep workers as separate processes**; and fix the CAS substrate first. Full reasoning in §6.

**Health snapshot** (run during this review):
| Check | Result |
|---|---|
| `pytest` | **14 failed**, 1206 passed (planner-orchestrator hook tests, `run_planning_session` regression tests) |
| `ruff check src tests` | **66 errors** (21 auto-fixable) |
| `mypy src` | **419 errors** in 91 files — despite `strict = true` in pyproject and the "strictly typed" claim in CLAUDE.md |

---

## 2. What Is Good (keep it)

- **Layering is actually enforced.** Domain is pure; app uses ports; infra adapts. Verified by import scan, not just by convention.
- **State discipline.** `_bump()`/history on every transition, `update_if_version` + bounded retry everywhere it matters (orchestrator `_on_task_assigned`, reconciler `_fail`).
- **Crash-conscious file I/O.** `AtomicFileWriter` (tmp + fsync + rename + dir fsync), corrupt-file quarantine in `list_all()`.
- **Dry-run mode as a first-class mode** wired through the container rather than `if` statements in business logic.
- **Frontend direction is right.** React Query for server state + Zustand for UI-only state is the correct split; `openapi-ts` generated types (`types.gen.ts`) remove the domain-parity drift problem.
- **Self-documenting API.** Routers carry real OpenAPI descriptions; `_unique_operation_id` produces clean generated-client names.

---

## 3. Major Issues & Design Flaws (Backend)

### 3.1 CRITICAL — `task.assigned` consumer-group misuse drops assignments

`src/infra/cli/system/commands.py:222-232`: every worker subscribes with `group="workers"`. Redis Streams consumer groups deliver each message to **exactly one** consumer in the group. The event payload targets a specific `agent_id`, but any worker can receive it; the wrong worker does:

```python
if assigned_to != agent_id:
    log.info("worker.skip_not_mine", ...)
    events.ack(event, group="workers")   # ACKed → gone forever
    continue
```

The assignment is then lost until the reconciler notices a stuck ASSIGNED task (default ≥120 s) and republishes — after which the task-manager re-assigns and the roulette spins again. With 3 workers, expected delivery latency is multiple reconciler cycles. This is the single most impactful bug in the system.

**Fix options (either is small):**
- Per-agent streams: publish to `events:task.assigned:{agent_id}`; each worker subscribes to its own stream.
- Or per-agent groups: each worker uses `group=f"worker-{agent_id}"` on the shared stream and skips non-matching events *without* starving others (every group gets every message).

### 3.2 CRITICAL — File-based CAS vs. multi-process topology

`src/infra/fs/task_repository.py:30-37` says it plainly:

> `update_if_version()` … is NOT a true atomic CAS against concurrent processes. It is ONLY safe if exactly one orchestrator process runs at any given time.

But `system start` spawns **api + task-manager + N workers + reconciler** (plus `goals run` separately), and at least four of those write task/goal state through this repository. Concrete race: reconciler reads task v5 → decides FAIL_LEASE_EXPIRED → worker CAS-writes v6 (success) → reconciler's `update_if_version` re-reads, sees v6 ≠ 5 and returns False — that path is guarded, but the read-compare-write *inside* `update_if_version` itself is not atomic: two processes can both read `state_version == 5` and both pass the compare before either rename lands. Last write wins; one transition (and its history entry) is silently lost.

**Fix:** one of
- Move hot state (tasks, goals) to Redis with a Lua compare-and-set (Redis is already a hard dependency), keeping YAML as an export/journal format; or
- SQLite with `BEGIN IMMEDIATE` transactions (single file, real multi-process locking, zero new infra); or
- Enforce single-writer architecturally: only the coordinator process writes aggregates; workers report results exclusively via events (today workers CAS task state directly in `TaskExecuteUseCase`).

The third option pairs naturally with the process-topology recommendation in §6.

### 3.3 HIGH — SSE layer: single queue, no Redis bridge, thread-unsafe publish

`src/api/sse.py` + `src/api/routers/events.py`:

- **One global `asyncio.Queue` shared by all SSE clients.** Each `queue.get()` consumes the event for one client only. Two browser tabs → each sees ~half the events. There is no fan-out.
- **No Redis → SSE bridge exists.** `publish_sse` is called only from API routers and the planner hook (verified by grep). Everything that happens in worker / task-manager / reconciler / goal-orchestrator processes — i.e., *all actual execution progress* — never reaches the frontend. The frontend's `task.status_changed` handler (`frontend/src/lib/queries.ts:261`) is dead code; the UI only updates via 30 s staleTime expiry and window-focus refetch.
- **Thread-safety violation:** routers are sync `def` handlers, so FastAPI runs them in a threadpool — `publish_sse` → `asyncio.Queue.put_nowait` from a non-loop thread can corrupt waiter state (`_wakeup_next` → `Future.set_result` off-loop).

**Fix (one coherent design):** a lifespan background task that `XREAD`s `events:all` (every API instance reads everything — no group), maps domain events to SSE event types (`task.* → task.status_changed`, etc.), and fans out to a registry of per-client `asyncio.Queue`s created in the `/events` handler. `publish_sse` from sync code should use `loop.call_soon_threadsafe`.

### 3.4 HIGH — Goal orchestrator is not part of `system start`; PR-enabled variant is dead code

- `system start` boots api, task-manager, workers, reconciler — **not** `TaskGraphOrchestrator`. The operator must separately know to run `orchestrator goals run`. Without it: completed tasks are never merged into goal branches, `goal.ready_for_review` is never acted on, JIT planning never fires. Nothing in `system start` output hints at this.
- `goals run` uses `container.task_graph_orchestrator`, which is built with `create_pr_usecase=None` (`src/infra/container.py:608`) — PR creation is **disabled** in the only code path that runs the orchestrator. `task_graph_orchestrator_with_pr` (container.py:614) is referenced nowhere.

**Fix:** add the orchestrator to `system start` (or fold it into the API process, §6), make the PR-enabled variant the default when GitHub is configured, and delete the other.

### 3.5 HIGH — No pending-message recovery for Redis Streams

`RedisEventAdapter.subscribe_many` reads only `">"` (new messages). If a consumer crashes after delivery but before `ack`, the message sits in the Pending Entries List forever — no `XPENDING` scan, no `XAUTOCLAIM`, and restarting with the same consumer name still reads only `">"`. The reconciler's republish pass papers over this for task events but nothing rescues goal/PR events (`goal.ready_for_review` lost → goal stuck until manual `pr/create`).

**Fix:** on consumer startup, claim and replay own pending messages (`XREADGROUP` with `id="0"` once before switching to `">"`, which also reuses the existing code path), plus a periodic `XAUTOCLAIM` for messages pending longer than a threshold.

### 3.6 MEDIUM — Discovery session API is fragile by construction

`src/api/routers/discovery.py`:

- Module-level globals (`_question_q`, `_answer_q`, `_discovery_active`) — one session per process, and **`_discovery_active` is never reset on the failure path**: if `orchestrator.start_discovery` raises in the executor, `await future` re-raises out of the handler and the flag stays `True` → every subsequent start returns 409 until the server restarts.
- **Timeout-as-protocol:** "done" is signaled by *not receiving a question within 60 s*. A slow LLM turn (> 60 s) makes `/message` falsely report `done=true`; the still-running session then pushes its next question into `_question_q` and the protocol desynchronizes.
- The executor future is discarded in `/message`, so when discovery completes through that path **the final brief is unreachable** (`DiscoveryMessageResponse(question=None, done=True)` carries no brief).
- `io_handler`'s `_answer_q.get()` has no timeout — an abandoned session leaks a blocked threadpool thread forever.

**Fix:** make completion an explicit signal (sentinel pushed into `_question_q` by a wrapper around `start_discovery`), reset state in a `finally`, key state by session id, and put a timeout on `_answer_q.get()`.

### 3.7 MEDIUM — Multi-minute LLM sessions inside synchronous request handlers

`POST /plan/refine`, `/plan/approve-architecture`, `/plan/approve-phase` run full planner sessions synchronously in the request. Consequences: requests that hang for minutes (browser/proxy timeouts), threadpool slot consumption (default ~40), no progress feedback (the SSE hook helps only if the planner emits events), no cancellation. The frontend compensates with a spinner (`isThinking`) and nothing else.

**Fix:** job-style endpoints — `POST` returns `202` with a session id immediately; progress streams over SSE (the planner hook already exists); a `GET /plan/sessions/{id}` returns the outcome. This also collapses the discovery special-casing in §3.6 into one pattern.

### 3.8 MEDIUM — Container/DI issues

`src/infra/container.py`:

- **Stale-spec caching:** `current_spec`, `validate_against_spec_usecase`, `planner_context_assembler`, `sync_goal_pr_usecase` all capture the spec at first access and never reload. `spec apply` while the API is up → API keeps validating against the old spec until the project/mode fingerprint changes.
- `DynamicContainerProvider` re-reads `.orchestrator/config.json` from disk **on every request** under a lock (server.py:67-72), and discarded containers leak their Redis connection pools (no `close()`).
- `build_interactive_planner_runtime` (container.py:260) is annotated `-> PlannerRuntimePort` — a name **never imported** in the module (only survives because of `from __future__ import annotations`; mypy flags it). It also reaches two levels into private internals: `runtime._runtime._io_handler = io_handler`. That's the DI container depending on an adapter's private layout.
- `sync_goal_pr_usecase` swallows *all* exceptions while loading the spec (`except Exception: pass`) — config errors become silent behavior changes.
- Two near-identical orchestrator factories (`task_graph_orchestrator` / `_with_pr`) differing in one argument; one is dead (§3.4).

### 3.9 MEDIUM — `system start` is a supervisor without supervision

- A child crash produces `warn(...)` **every 2 seconds forever** (commands.py:136-140); no restart, no backoff, no removal, no exit-on-critical-child.
- Dry-run + `system start` is documented (CLAUDE.md) but structurally broken: each child process gets its own `InMemoryEventAdapter`, whose `subscribe_many` yields the (empty) backlog and **returns** — so every daemon exits immediately, and the supervisor warn-spams. The in-memory adapter's generator semantics (finite replay) are incompatible with the Redis adapter's (infinite blocking) — same port, different contract.

### 3.10 LOW — Assorted

- **Reconciler:** `_fail` does `self._repo.load(task.task_id)` which raises `KeyError` if the task was deleted mid-pass (the per-task try/except catches it, but it logs as an "error" pass forever). PR polling is sequential per goal per pass — fine for now, worth a note for fleets.
- **`RedisEventAdapter._pending`** grows unboundedly if callers skip `ack` (the worker acks not-mine events, but any handler exception path leaks the entry).
- **Event journal** writes one small JSON file per event with no rotation/pruning — unbounded inode growth on long runs.
- **CORS** is hardcoded to `localhost:5173` (server.py:129) rather than coming from settings.

---

## 4. Dead Code, Duplication, and Packaging

| Item | Evidence | Action |
|---|---|---|
| `src/infra/github/github_client.py` (377 LOC) | No imports anywhere; `client.py` is the live adapter. The two have **divergent retry/rate-limit policies** in their docstrings | Delete |
| `src/cli.py`, `src/dependency_checker.py` | Back-compat shims; the latter is still imported by 2 test files and one *docstring* | Update test imports, delete shims |
| `task_graph_orchestrator_with_pr` | Never referenced | Make it the default, delete the other |
| `RunPlanningSessionUseCase` | Emits its own DeprecationWarning; its regression tests are among the 14 failures | Delete with its tests |
| `plan decision` command | Prints "(not fully implemented in this prototype)" | Implement or remove |
| **pyproject dependencies** | `pytest`, `mypy`, `ruff`, `fakeredis`, `coverage`, `pytest-cov` are pinned in **runtime** `dependencies` *and* listed again in `[dev]` with different constraints; `rq`, `prometheus-client`, `tenacity`, `python-ulid` are declared but **never imported** | Move dev tools to `[dev]`, drop unused deps |
| **No `[project.scripts]`** | CLAUDE.md says `orchestrate`, CLI error messages say `orchestrator init`, reality is `python -m src.infra.cli.main` | Add `orchestrate = "src.infra.cli.main:cli"` and unify the name everywhere |
| Repo layout drift | CLAUDE.md documents `orchestrator-fullstack/backend/src/...`; actual layout is `src/` + `frontend/` at root | Fix CLAUDE.md |

---

## 5. Frontend ↔ Backend Integration: Analysis & Recommended Architecture

### What exists today

- **Read path:** REST + React Query (`usePlan`, `useGoals`, …) with `staleTime: 30s` and window-focus refetch. Solid.
- **Types:** `openapi-ts` generation (`types.gen.ts`, 1.8k lines) — the right call; `scripts/export_openapi.py` closes the loop.
- **Live path:** `useSSEBridge` translating SSE events into cache invalidations. The *pattern* is correct; the *feed* is broken (§3.3): only API-initiated mutations produce SSE events, so the canvas does not actually update live while workers execute.
- **Chat routing:** the frontend replicates the plan state machine (`sendChatMessage` switches on `planStatus` read from the *cache*) to decide which endpoint to call. If the cached status is stale, messages go to the wrong endpoint.

### Recommended target architecture

1. **One event spine.** Domain events (Redis `events:all`) are the source of truth for "something changed". The API hosts a lifespan task that consumes `events:all` and fans out to per-client SSE queues (§3.3 fix). `publish_sse` call sites in routers can then be deleted — routers publish *domain* events (most use cases already do) and the bridge handles the UI. One path, no vocabulary drift.
2. **Event contract as code.** Define the SSE event union once in the backend (a discriminated Pydantic union exported through OpenAPI components) so `SSEEvent` in `api.ts:158` is generated, not hand-maintained — it has already drifted.
3. **Resync on reconnect.** `EventSource` auto-reconnects, but events emitted during the gap are lost. On `open` after an error, invalidate all query keys (one-liner in `useSSEBridge`); keep `staleTime` as the safety net.
4. **Move chat routing server-side.** A single `POST /api/chat` that consults the *authoritative* plan status and dispatches to refine/discovery/advisory removes the duplicated state machine and the stale-cache misroute.
5. **Generate the client too.** You already run `openapi-ts` for types; generating the service layer would replace the hand-rolled `post`/`get` helpers and keep paths in sync (e.g., when a route gains a query param).
6. **Long operations become sessions** (§3.7): `202 + session_id`, progress via the same SSE spine (`plan.jit_progress` already exists as a precedent), terminal result fetched by query. The discovery Q&A flow fits this exactly and sheds its global-queue hack.

---

## 6. Process Architecture: workers in the API? RQ? Keep daemons?

### The actors and what they actually do

| Process | Workload | Blocking? | Writes state? |
|---|---|---|---|
| Task manager | Routes 4 event types → assign/unblock/fail use cases | Blocking Redis reads, ms-scale work | Yes (tasks) |
| Goal orchestrator | Routes 7 event types → merge/cancel/PR/JIT use cases | Blocking reads; JIT planning calls an LLM (minutes) | Yes (goals) |
| Reconciler | 60 s poll: lease/heartbeat checks + GitHub PR sync | Sleep loop | Yes (tasks, goals) |
| Worker (×N) | Spawns a coding-agent **subprocess** for up to `task_timeout` (10+ min), git workspace per task, test runs | Heavy, long, multi-resource | Yes (tasks) |
| API | HTTP + SSE (+ today: inline LLM sessions) | Should not be | Yes (plan, goals via approvals) |

### Verdict on RQ (or any job-queue library)

**Not recommended.** Reasons, concretely:

- **You already own the machinery RQ would provide.** Leases (`LeasePort` + `LeaseRefresher`), heartbeats, retry policy (`task.retry_policy`), a scheduler (`SchedulerService`), a dead-letter equivalent (FAILED + reconciler), and delivery via Redis Streams consumer groups. Adopting RQ means either running two overlapping reliability systems or rewriting the domain's retry/lease model around RQ's job lifecycle — a large diff for zero new capability.
- **RQ's model fits "function + args → result", not "agent session with live log streaming, lease renewal, and workspace lifecycle".** You'd end up wrapping `TaskExecuteUseCase.execute` in a job and re-plumbing `LiveLogger`/telemetry through RQ's forked-worker model (RQ forks per job; your runtime wrapper threads and heartbeat threads don't survive that cleanly).
- **It fixes nothing on the actual critical path.** The bugs are consumer-group misuse (§3.1), missing PEL recovery (§3.5), and non-atomic file CAS (§3.2). RQ addresses none of them — task *state* would still live in YAML files.
- The event-driven shape (workers react to `task.assigned`, coordinator reacts to `task.completed`) is the right architecture for this domain; Streams is the right primitive. What's missing is ~50 lines of recovery code, not a framework.

If the system ever goes multi-machine/multi-tenant, skip RQ and go to Postgres-backed state + `SELECT … FOR UPDATE SKIP LOCKED` task claiming (or keep Streams and move CAS to Redis Lua). That's the grown-up version of this architecture.

### Recommended topology

**Fold the three coordinators into the FastAPI process; keep workers external.**

```
┌─ orchestrate system start ──────────────────────────────┐
│  uvicorn (FastAPI)                                       │
│   ├─ HTTP routers + SSE fan-out                          │
│   ├─ lifespan thread: events:all → SSE bridge            │
│   ├─ lifespan thread: TaskManagerHandler loop            │
│   ├─ lifespan thread: TaskGraphOrchestrator loop         │
│   └─ lifespan thread: Reconciler loop                    │
│                                                          │
│  worker processes (subprocess, one per active agent)     │
│   └─ agent CLI subprocess + git workspace                │
└──────────────────────────────────────────────────────────┘
```

Why this split:

- **Coordinators are thin event routers** (ms-scale handlers, the JIT-planner LLM call being the one exception — run it on an executor). They gain a lot from co-location: one container, one settings load, direct access to the SSE bridge, no supervisor, no heartbeat choreography at boot, and **one writer process** for goal/plan state — which makes the YAML CAS constraint satisfiable for goals/plan immediately.
- **Workers must stay separate processes.** They spawn arbitrary coding-agent subprocesses that consume CPU/RAM for many minutes, can hang, and must be killable without taking the API down. Process isolation is the feature, not the cost. (`system start` keeps spawning exactly these, and only these.)
- Implementation is mechanical: the `subscribe_many` loops are blocking generators, so run each in a `threading.Thread(daemon=True)` started/stopped via FastAPI's lifespan; `shutdown()` flags already exist on the orchestrator and can be added to the other two loops. No async rewrite required (it can come later by making `EventPort` async).
- To finish the single-writer story, stop workers writing task state directly: `TaskExecuteUseCase` publishes `task.started/completed/failed` events with result payloads, and the in-API task manager becomes the sole task-state writer. That converts §3.2 from "replace the storage engine" into "tighten one use case" — and YAML stays viable for the single-user tool this is today.

`system start` then reduces to: dep-check → start uvicorn → spawn workers → supervise *only workers* (with restart-with-backoff, fixing §3.9).

---

## 7. CLI Review & Refactoring Plan

The command structure (group-per-resource, thin commands calling use cases, `@catch_domain_errors`) is sound. The issues are in the implementations:

### Bugs

1. **`plan architect` "edit" flow discards the edit** (`plan/commands.py:274-283`): the decision is written to a temp file, the editor runs, then the file is **unlinked without being read back**, and the edited decision is also not added to `approved_ids` — the operator's work is silently thrown away.
2. **`os.system(f"{editor} {temp_path}")`** — shell injection via `$EDITOR` and no exit-code check. `click.edit(decision.content)` does the whole job safely in one line.
3. **Spinner + interactive prompt collision:** `_spinner("Running discovery session")` stays animated while `_io_handler` calls `click.prompt` — the spinner thread keeps printing `\r` frames over the question the user is trying to answer.
4. **`plan status` hardcodes "JIT Planner: enabled"** regardless of configuration, and wraps goal listing in `except Exception: pass`.

### Structure

5. **Container lifecycle:** `_require_project()` builds a full `AppContainer` and throws it away; the command body builds another (each = settings load + dir creation). Build once in the `cli` group callback and pass via `ctx.obj` (click's intended pattern); commands receive it with `@click.pass_obj`.
6. **`os.environ["AGENT_MODE"] = "dry-run"`** inside commands violates the project's own "no os.environ deep in the code" rule and acts at a distance. Pass mode into `AppContainer.from_env(mode=...)`.
7. **Repeated boilerplate** — the mode-warning block (`goals init`/`goals run`), the planner-logger setup + hook binding + session_start/end/close choreography (3× in plan commands) → extract a `planner_session(container, mode)` context manager.
8. **`plan logs` calls `live._render_to_terminal(event)`** — private API; give `LiveLogger` a public `replay(events)`.
9. **Unused locals** (`session_id`, `_start` in `plan review`), duplicate import of `AppContainer` under two names (`plan/commands.py:25-26`).
10. **`plan review` approval prompt logic is wrong-ish:** `if approve_next or click.confirm("Mark project as done?")` — answering "no" to *continue* then "no" to *done* silently does nothing, with no message.

### Packaging / naming

11. Add `[project.scripts]` (`orchestrate = "src.infra.cli.main:cli"`); pick **one** name (`orchestrate` vs `orchestrator`) and fix all help/error text.
12. Delete `src/cli.py` shim; repoint the two test files importing `src.dependency_checker` and delete that shim.
13. `system start` supervisor: restart-with-backoff, warn once per crash, and (per §6) supervise only workers.

---

## 8. Prioritized Action Plan

| # | Action | Severity | Effort |
|---|---|---|---|
| 1 | Fix `task.assigned` delivery (per-agent stream or group) | Critical | S |
| 2 | Single-writer task state: workers publish results, task-manager writes | Critical | M |
| 3 | Redis→SSE bridge + per-client fan-out + thread-safe publish | High | M |
| 4 | Start goal orchestrator from `system start` (or embed per §6); default to PR-enabled variant | High | S |
| 5 | PEL recovery: replay own pending on startup + periodic `XAUTOCLAIM` | High | S |
| 6 | Embed coordinators in API lifespan; slim `system start` to worker supervision with restarts | High | M |
| 7 | Discovery/refine endpoints → session pattern (202 + SSE progress); fix `_discovery_active` leak now even if the rest waits | Medium | M |
| 8 | Fix CLI `architect` edit flow + `click.edit`; container-once via `ctx.obj` | Medium | S |
| 9 | Delete dead code (`github_client.py`, shims, `_with_pr` twin, `RunPlanningSessionUseCase`); clean pyproject deps; add `[project.scripts]` | Medium | S |
| 10 | Spec-staleness: drop `cached_property` for spec-derived members or add invalidation on `spec apply` | Medium | S |
| 11 | Green the build: 14 failing tests, 66 ruff, 419 mypy | Medium | M |
| 12 | SSE reconnect resync + generated API client + server-side chat routing | Low | M |

*Effort: S < ~half day, M = 1–3 days.*
