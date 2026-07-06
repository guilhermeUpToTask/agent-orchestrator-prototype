# Known issues & fragile spots

*Verified against the code on 2026-07-06 (branch `refactor/domain`) during a full-codebase archaeology — every entry cites `file:line` under `backend/src/`. Fixes are scheduled in [ROADMAP.md](../../ROADMAP.md); the full analysis (stress tests, alternatives considered) is archived as the [evolution plan](../history/planning/2026-07-06-orchestrator-evolution-plan-fable-5.md).*

> When you fix one of these, delete its entry here and add the regression test that locks it.

## 🔥 Defects (hotfix candidates)

### H1 — Double-execution window: lease < task timeout, no mid-run heartbeat

The documented invariant — *"lease_seconds must exceed the longest expected single task run"* (`infra/worker/main.py:34-37`) — is violated by the shipped defaults, and nothing enforces it:

- Default lease: **300s** (`infra/cli/main.py:94-99`); default subprocess timeout: **600s** (`infra/runtime/factory.py:76`).
- Heartbeats happen only **between** units (`app/use_cases/run_worker.py:60`) — never during the `await runner.run(...)` in `app/handlers/execution_handler.py:109`.
- Bonus inconsistency: `worker_tick`'s own default is `lease_seconds=60` (`run_worker.py:74`).

**Failure scenario:** with ≥2 workers (or a restart racing a live one), a task running >300s expires its lease; another worker claims the plan, sees the task RUNNING with no result, re-picks it (`start_task` legally accepts RUNNING → RUNNING, `domain/entities/task.py:31-36`), and executes it **concurrently** — duplicate LLM spend and two merges of the same work.

### H2 — Poisoned-plan starvation (single-worker head-of-line blocking)

Three correct-looking pieces compose into starvation:

- The claim picks the **oldest `updated_at`** (`infra/db/plan_repository.py:68`).
- A tick exception is caught and survived (`infra/worker/main.py:77-84`) — good — but the plan's `updated_at` never changed (nothing saved), and `release()` doesn't touch it (`plan_repository.py:83-90`).

**Failure scenario:** a plan whose drive raises before any save (unreconstructable JSON, a use-case bug, a deleted-out-from-under reference) is re-claimed **first, every poll tick, forever**. On the standard single-worker deployment, every other plan starves behind it.

## Silent-death spots (crash windows an operator can't see)

1. **Worker dies mid-agent-run** — nothing observable until lease expiry (≤300s); the task shows RUNNING with no event after `TaskStarted`.
2. **Crash between the git merge and finalize** — `workspace.commit(handle)` (`execution_handler.py:116`) precedes the finalize transaction (`:266-289`). A crash in that gap leaves the merge in `plan/<id>` but the task RUNNING with `result=None`; the re-pick re-runs the whole task and **merges the same work twice**. The check-before-act guard (`:100`) covers only the persisted-result window, not the committed-git window.
3. **SSE queue overflow drops silently** — a slow client's queue (max 200) drops events with only a server-side warning (`api/sse.py:83-84`); there is no replay on reconnect (the frontend's blanket refetch is the compensation).
4. **Requeue erases the attempt's result** — `task.requeue()` sets `result = None` (`domain/entities/task.py:58`); the failure trail survives only in outbox event payloads. This was decision #4 of the domain freeze ("history lives in events") — it now hurts operations and is scheduled to change.
5. **Relay thread crash-loops invisibly** — events stop flowing but the API stays healthy; log-only signal (`api/outbox_relay.py:102-104`).

## Operational debt

- **Agent-events full replay on API boot** — the relay's cursor starts at 0 every startup (`api/outbox_relay.py:96`), re-publishing the entire `agent_events` table to connected SSE clients; combined with **no retention on any event/chat table**, boot cost grows without bound.
- **Git debris** — no `git worktree prune` anywhere; a crash mid-attempt leaks the worktree dir and its branch ref (`infra/git/workspace.py:107-137`); `plan/*` branches are never garbage-collected. Self-healing-ish (a retry uses a new attempt number → new branch), but `git worktree list` grows across crashes.
- **Delete-guard by JSON substring** — `_referenced_by_active_plan(s, '"agent_id":"…"')` (`infra/db/reference_repos.py:246`) string-matches the plan document. Safe today (exact-id match; false positives only block a delete), but it silently depends on Pydantic's compact JSON serialization.

## Bug-magnet zones (where a change is most likely to introduce a defect)

1. **The two-transaction choreography** (`execution_handler.py:81-121`) — never let a live aggregate reference cross a transaction boundary (that's what the frozen `_Unit` snapshot is for, `:53-64`), and never skip the re-read + re-guard in a finalize.
2. **Manual `bump_version()` before every `save()`** (~12 call sites, e.g. `app/use_cases/control.py:24`, `conversation.py:138`) — forgetting it is a runtime `StaleVersionError` no type checker catches.
3. **The skip/abandon/fail status lattice** (`domain/entities/task.py:61-73`, `goal.py:38-48`) — the guards differ deliberately; requeueing into an abandoned iteration is the resurrection bug the tolerant finalize (`execution_handler.py:216-233`) exists to prevent. Route every new transition through the aggregate.

## Magic numbers (working, but conventions — not laws)

| Value | Where | Meaning |
|---|---|---|
| `max_steps=10_000` | `run_worker.py:44` | drive-loop hard stop |
| 8 000 chars | `cli_runner.py:37` | TaskResult stdout tail |
| queue 200, drop-on-full | `sse.py:24` | per-SSE-client buffer |
| 0.5s | `outbox_relay.py:91` | relay poll |
| 5 000 ms | `db/engine.py:31` | SQLite busy timeout |
| `position=10**6` | `api/routers/plans.py:104` | add-task sentinel, renumbered by the edit service |
| `seq` ∈ {0, 1} | `cli_runner.py:92,105,119` | agent events are start/finish only — streaming is a roadmap seam |
