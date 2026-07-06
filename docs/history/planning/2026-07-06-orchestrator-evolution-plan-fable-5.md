# PLAN.md — Orchestrator Evolution Plan

*Written 2026-07-06 on branch `refactor/domain`, as a letter to the future maintainer. Every claim cites `file:line` in `backend/src` unless noted.*

## Part 0 — Archaeology

### Control flow (as actually implemented)

```
                 ┌──────────────────────────── HUMAN / FRONTEND ───────────────────────────┐
                 │  POST /plans (create)      chat messages         gate commands           │
                 └───────┬───────────────────────┬──────────────────────┬──────────────────┘
                         v                       v                      v
                   create_plan            conversation.py          control.py
                   (DISCOVERY)     DISCOVERY/REPLANNING turns   approve/finish/replan
                         │          commit -> ARCHITECTURE           │
                         └──────────────┬────────────────────────────┘
                                        v
   plans table (ONE JSON document + promoted phase/version/lease columns, tables.py:35)
                                        ^
                                        │ claim_one_unit: UPDATE..RETURNING, phase ∈
                                        │ {ARCHITECTURE, ENRICHING, RUNNING}, lease NULL/expired
                                        │ (plan_repository.py:57-73)
        ┌───────────────────────────────┴───────────────────────────────┐
        │ WORKER (one process, one plan at a time)                      │
        │ run_worker_forever (worker/main.py:64) — 1s poll              │
        │   worker_tick (run_worker.py:66) claim → drive_plan loop      │
        │     advance_plan dispatcher (advance_plan.py:57)              │
        │       RUNNING   → ExecutionHandler  (pull-scan, 2-txn)        │
        │       ARCH/ENR  → PlanningHandler   (passthrough / JIT enrich)│
        │       gates     → GateHandler       (always PAUSED)           │
        │   heartbeat BETWEEN units only (run_worker.py:60)             │
        └───────┬───────────────────────────────────────────────────────┘
                │ side effects OUTSIDE txns
                v
   GitBranchWorkspace begin/commit/discard          CLI runners (pi/claude/gemini)
   worktree on task/<id>/a<n> off plan/<id>         subprocess, timeout 600s
   (workspace.py:107-180)                           (cli_runner.py:127-168)

   EVENTS: state txn writes outbox rows (uow.outbox.add) ──► relay thread polls 0.5s
   (outbox_relay.py:86-107) ──► SSEBroker per-client queues (sse.py:41-84) ──► GET /api/events
   agent_events tailed by cursor from 0 each boot (outbox_relay.py:96) ──► "agent.event"
```

### One task's life story

Enriched into a goal (`planning_handler.py:95-123`) → bound to an agent by capability (`planner_orchestrator.py:240-253`) → gate approved → the pure scan selects it (`navigation.py:29-69`) → txn1 marks it RUNNING and emits `TaskStarted` (`execution_handler.py:171-199`) → a worktree is created on `task/<id>/a<attempt>` → the CLI subprocess runs → `workspace.commit` merges `--no-ff` into the plan branch (`workspace.py:139-171`) → txn2 re-reads, re-guards, records DONE (`execution_handler.py:266-289`). Failure classifies through the taxonomy (`taxonomy.py:50-56`) into requeue-with-backoff or terminal FAILED (`execution_handler.py:210-264`); a failed goal halts the plan (`planner_orchestrator.py:151-155`).

**Where it can die silently:**

1. Worker dies mid-agent-run: nothing observable until lease expiry (up to 300s); the task shows RUNNING with no event after `TaskStarted`.
2. Crash between `workspace.commit(handle)` (`execution_handler.py:116`) and `_finalize_success` txn2: the git merge exists but the task is still RUNNING with `result=None` — the re-pick re-runs the whole task and merges it **again** (check-before-act at `execution_handler.py:100` only protects the persisted-result window, not the committed-git window).
3. SSE client queue full → event silently dropped for that client (`sse.py:83-84`); no replay on reconnect (delivered rows are never re-sent).
4. Requeue **erases the failed attempt's result** (`task.py:58` sets `result = None`) — the failure trail survives only in outbox payloads.
5. Relay thread crash-loops → UI events stop, API stays healthy; log-only signal (`outbox_relay.py:102-104`).

### Load-bearing hacks (each verified)

| # | What | Where |
|---|------|-------|
| 1 | Lease default 300s vs agent subprocess timeout default 600s — defaults violate the documented invariant "lease must exceed the longest task run" | `cli/main.py:94-99`, `runtime/factory.py:76`, invariant at `worker/main.py:34-37` |
| 2 | No heartbeat during an agent run — only between units | `run_worker.py:60` |
| 3 | `worker_tick` default `lease_seconds=60` disagrees with the 300 used everywhere else | `run_worker.py:74` |
| 4 | Poisoned-plan reclaim: claim `ORDER BY updated_at` + catch-all tick recovery + `release()` not touching `updated_at` → the same failing plan is re-claimed first every 1s | `plan_repository.py:68`, `worker/main.py:77-84`, `plan_repository.py:83-90` |
| 5 | `agent_cursor = 0` on every relay start → whole `agent_events` table re-published to SSE each API boot; table never purged | `outbox_relay.py:96`, no purge anywhere in `src/` |
| 6 | Delete-guard by JSON substring match: `_referenced_by_active_plan(s, f'"agent_id":"{agent_id}"')` | `reference_repos.py:246` |
| 7 | `position=10**6` sentinel renumbered later | `api/routers/plans.py:104` |
| 8 | Magic numbers: `max_steps=10_000` (`run_worker.py:44`), output tail 8 000 chars (`cli_runner.py:37`), SSE queue 200 drop-on-full (`sse.py:24`), relay poll 0.5s (`outbox_relay.py:91`), busy_timeout 5s (`engine.py:31`) | (as cited) |
| 9 | `seq` is hardcoded 0/1 (start/finish) — the streaming-events seam is a stub | `cli_runner.py:92,105,119` |
| 10 | Orphan git debris: no `worktree prune`; plan branches never deleted; crash leaves stale worktrees holding branch refs | `workspace.py:107-137` |

Notably: **zero TODO/FIXME markers in the entire codebase** — the hacks live in docstrings and defaults, not comments.

### Top 3 bug-magnet spots for a new engineer

1. **The two-transaction choreography** (`execution_handler.py:81-121`): holding a live aggregate ref across the txn boundary instead of the `_Unit` snapshot (`:53-64`), or forgetting the re-read + re-guard in a finalize.
2. **Manual `bump_version()` before every `save()`** (~12 call sites, e.g. `control.py:24`, `conversation.py:138`): forget it and you get a runtime `StaleVersionError` that no type checker catches.
3. **The replan/tolerant-finalize status lattice**: `skip` vs `abandon` vs `fail` have deliberately different guards (`task.py:61-73`, `goal.py:38-48`); requeueing into an abandoned iteration is the resurrection bug the guards exist to prevent (`execution_handler.py:216-233`).

## Part 1 — Stress tests (against current code)

**10x test.** First break: throughput — one worker process drives exactly one plan at a time to exhaustion (`worker_tick` claims one, `drive_plan` loops it; `worker/main.py` runs a single loop), and tasks inside a plan are serial by design. 10x concurrent plans queue behind one 600s subprocess. Second break: SQLite single-writer with whole-document CAS writes — every task transition rewrites the full plan JSON (`plan_repository.py:122-139`, schema decision `tables.py:4-10`); concurrent workers + API edits contend on the 5s busy_timeout. Third: unbounded `outbox`/`agent_events`/`plan_chat_messages` growth and the boot-time full-table agent-events replay (hack #5).

**Chaos test.** Worker dies mid-task: lease expires (≤300s), any worker re-claims; `start_task` accepts RUNNING re-pick (`task.py:31-36`) and re-runs — correct, but see silent-death #2: if the dead worker had already merged the workspace, the re-run merges duplicate work. Duplicate SSE delivery: handled, consumers dedup on `event_id` (`outbox_relay.py:55`). Downstream hang 90s: the subprocess timeout (600s) eventually fires and classifies TIMEOUT (`cli_runner.py:139-143`) — but the single worker is fully blocked meanwhile; and **with two workers a >300s task exceeds its lease and gets claimed and executed a second time, concurrently** (hack #1+2 combined). That's the standing chaos failure.

**3am test.** Operator CAN see: structured logs, live SSE, the full plan document (`GET /api/plans/{id}`), `GET /api/runner/status`, `claimed_by` in summaries. CANNOT see: attempt history (erased on requeue, silent-death #4), failed attempts' stdout (only `reason[-500:]` in an event payload, `cli_runner.py:160`), worker liveness, queue depth, any timeline of a task, which git branch holds a task's work. There are no metrics of any kind.

**New-feature test (coupling score).** Adding a new CLI runtime touches ~4 backend files: `cli_runner.py` (new class), `runtime/factory.py` (`RUNTIME_TYPES` at `:73` + dispatch `:251-266`), `runtime/dependency_checker.py`, plus frontend settings — acceptable. Adding a new *phase* is deliberately heavy (frozen enum + claim predicate + dispatcher + handler + frontend rail); that's a feature, not a bug.

**Kill test (the ~20%).** `LocalDirWorkspace` (`workspace.py:190-209`, referenced nowhere outside its own module), the `publish_sse` back-compat shim (`sse.py:91-93`), the functional `advance_plan` wrapper once tests migrate to `PlanDispatcher` (`advance_plan.py:81-99`), and the duplicated lease/poll defaults scattered across three files. Honest assessment: this codebase is already unusually lean; the deletable 20% is mostly seams kept "for later".

## Part 2 — Three futures

**The Surgeon** (1–2 weeks, low risk). Fix the six operational defects without touching architecture: mid-run heartbeat + lease/timeout boot validation; poisoned-plan rotation (touch `updated_at` on release); persist the relay cursor; retention purge; stop erasing attempt history; `git worktree prune`. Makes possible: running `agent_runner.mode=real` safely with >1 worker. Forecloses: nothing.

**The Architect** (the design this system is secretly becoming): a task-level execution pool. Evidence it wants this: the unused DAG seam `depends_on` ("the DAG seam (unused in a chain)", `goal.py:12`), the driver-model claim predicate already separating "who advances what", mid-run heartbeats and Redis both deferred to "roadmap Phase 3" (`worker/main.py:36-37`, CLAUDE.md). Shape: a `task_runs` table claimed independently of the plan document; the plan scan becomes a scheduler that *enqueues* ready tasks; N executors run attempts in parallel across and within plans. Effort: 3–6 weeks. Risk: medium-high — plan-level CAS on one JSON document fights row-level task claims; needs narrower writes and a much bigger truth-test matrix. Makes possible: parallel goals/tasks, natural heartbeats, attempt history for free. Forecloses: the "one document is the whole truth" simplicity that currently makes the truth tests convincing.

**The Heretic**: replace the custom lease/loop/outbox with a durable-execution engine (Temporal, or DBOS to stay in-process). Steelman: heartbeats, retries, timers, at-least-once delivery, versioned workflows, and a visibility UI are exactly the things being hand-built here, and the two-transaction choreography — the #1 bug magnet — would dissolve into workflow determinism. Honest verdict: **not worth it**. This is a local-first tool whose deployment story is "SQLite file under `~/.orchestrator`"; Temporal adds a server dependency, DBOS a framework rewrite of the frozen domain; the human-gated 9-phase machine (the actual product) gets *harder* to test, and the existing dual-backend truth suite (`tests/support.py`) already provides the atomicity proof an engine would. Re-evaluate only if the Architect future's multi-executor pool is actually built and starts re-inventing workflow versioning.

## Part 3 — The plan (Surgeon now, two Architect stepping stones later)

🔥 **Hotfix candidates (independent of the roadmap, each one small PR):**

- **H1 — double-execution window**: defaults allow a 600s task under a 300s lease with no mid-run heartbeat (`runtime/factory.py:76` vs `cli/main.py:95`, `run_worker.py:60`).
- **H2 — poisoned-plan starvation**: a plan whose drive raises pre-save is re-claimed first every second forever, starving all other plans on a single-worker deployment (`plan_repository.py:68` + `plan_repository.py:83-90` + `worker/main.py:77-84`).

### Phase 1 — Safety (H1 + H2). Ships: safe real-mode execution.

Files: `run_worker.py` (spawn an `asyncio` heartbeat task around `drive_plan`, cancelled in `finally` — safe because the lease trio uses its own short sessions, `plan_repository.py:158-195`); `worker/main.py` (boot check: warn/clamp when `agent_runner.timeout_seconds ≥ lease_seconds`); `plan_repository.py` (`release()` sets `updated_at = now` so failing plans rotate to the back of the claim queue).
Tests: truth-suite additions — 700s scripted dummy task under a 300s lease with two workers ⇒ exactly one execution; poisoned plan + healthy plan ⇒ healthy plan advances within 2 ticks.
Rollback: revert; no schema change.
Success criteria: both new tests green on fake AND SQLite backends; zero double-claims in a 100-iteration chaos run.

### Phase 2 — Observability (the 3am fixes). Ships: answerable incidents.

Files: `task.py` (requeue keeps a bounded `attempt_history` list instead of erasing `result` — domain un-freeze needed, one field, same precedent as the 2026-07-05 AgentSpec un-freeze); `outbox_relay.py` (start `agent_cursor` at `MAX(id)`, or persist it in `config`); new `infra/cli` `db prune` command deleting delivered outbox rows and old agent_events; `api/routers/plans.py` add `GET /plans/{id}/tasks/{tid}/attempts`.
Migration: one alembic revision (additive); rollback = downgrade.
Success criteria: "what happened to attempt 2 of task X" answerable from the API alone; API restart publishes 0 stale agent events (test asserts); `outbox` row count bounded after prune.

### Phase 3 — Hygiene (the kill list). Ships: fewer lines, one config truth.

Delete `LocalDirWorkspace`, the `publish_sse` shim (after confirming no importers), unify the three lease/poll defaults into one constants module; add `git worktree prune` to `_begin_sync` and a `workspace gc` CLI for dead `plan/*` branches.
Success criteria: ≥150 lines deleted; `git worktree list` stable across a kill-mid-task chaos test.

### Phase 4 (evidence-gated) — First Architect stone. Ships: parallel plans done right.

Only if real usage shows plans queueing: document + truth-test the two-worker deployment (the code already supports it once Phase 1 lands), then evaluate goal-level parallelism via the existing `depends_on` seam. Explicitly NOT task-level pools yet.

### Do-not-do list

- **Temporal/DBOS/Celery/Redis** — see the Heretic verdict; Redis stays roadmap Phase 3 at the earliest.
- **Task-level parallelism now** — no throughput evidence; it breaks the plan-document CAS model for a speculative gain.
- **Splitting the plan JSON into relational goal/task rows** — the document + truth tests are the system's core asset.
- **Streaming NDJSON agent events (roadmap 2.4)** — tempting, but Phase 2's attempt history answers the actual 3am question first.
- **WebSockets to replace SSE / event replay on reconnect** — the frontend refetches state on connect; dedup already handles the rest.

### The first PR (start immediately)

**`fix(worker): heartbeat mid-run and enforce lease > task timeout`** — In `run_worker.py:drive_plan`, wrap the `advance_plan` await with a sibling `asyncio` task that calls `uow.plans.heartbeat(plan_id, worker_id)` every `lease_seconds // 3` seconds, cancelled in `finally`; in `worker/main.py:run_worker_forever`, after `validate_agent_runner_mode`, read `agent_runner.timeout_seconds` and log an error + clamp the effective lease to `timeout + 60` when it is smaller; thread `lease_seconds` into `drive_plan` (new parameter, default 300 — also fixes the 60-vs-300 inconsistency at `run_worker.py:74`). Tests: extend `tests/unit/orchestration/test_worker_loop.py` with a FakeClock-driven long-running scripted task asserting the lease never expires mid-run and that a second `claim_one_unit` returns `None`; run on both truth-test backends. ~80 lines, no migration, revert-safe.

### Adversarial self-review

- *"Heartbeat from a concurrent task against a non-thread-safe UoW?"* — the lease methods deliberately bypass the bound session (`plan_repository.py:158-195` run on own short sessions); only `get/save` are UoW-bound. Verified, not assumed.
- *"Is double-execution real or theoretical?"* — real only with ≥2 workers or a restart racing a live worker; but multi-worker is the documented crash-recovery story, so the defaults are still wrong.
- *"Your success metrics have no baseline."* — acknowledged: there is no production load yet, so criteria are pass/fail behavioral tests (double-claims = 0, stale replays = 0, lines deleted ≥ 150) rather than latency/MTTR numbers, which would be fiction today.
- *"Phase 2 un-freezes the frozen domain."* — yes, deliberately and minimally (one additive field on `Task`), following the documented precedent (`CLAUDE.md`, AgentSpec un-freeze 2026-07-05). The alternative (attempt history in `agent_events` only) loses transactionality with the state that produced it.
