# P4.2 — Operational truth: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "is a worker alive?", "is this block mine to fix?", and "does this advertised action actually work?" answerable over HTTP.

**Architecture:** One new table (`workers`) written best-effort off the event loop by a background heartbeat task; two additive projections on the plan document (`requires_human` on blocks, `action_endpoints` beside `legal_actions`); and a contract test that consumes the served endpoint map rather than a second copy of it.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 (`Mapped`/`mapped_column`), Alembic, asyncio, pytest (`integration` marker), structlog.

## Global Constraints

- **No domain change.** `src/domain/` is frozen. `requires_human` is a projection, never a persisted field.
- Migration **0016**, revises `0015_plan_delete_cascade` (the current head). One linear head — `test_migrations.py` fails otherwise.
- The `workers` table is **not** plan-scoped: no FK to `plans`, no cascade obligation.
- Heartbeat writes are **best-effort**: logged on failure, never propagated, never inside the plan UoW.
- Every SQLite write from the worker's event loop goes through `asyncio.to_thread` — see the self-deadlock comment in `src/infra/worker/main.py`.
- No `print()`, no stdlib `logging`. `log = structlog.get_logger(__name__)`, namespaced event names.
- `uv run ruff check src tests` and `uv run mypy src` clean. `from __future__ import annotations`.
- Run tests as `cd backend && timeout 300 uv run pytest <paths> -q --no-cov`.

---

### Task 1: The `workers` table and its registry

**Files:**
- Create: `backend/alembic/versions/0016_worker_registry.py`
- Modify: `backend/src/infra/db/tables.py` (append a `WorkerTable`)
- Create: `backend/src/infra/db/worker_registry.py`
- Test: `backend/tests/integration/test_worker_registry.py` (create)

**Interfaces:**
- Produces: `WorkerRegistry(session_factory)` with
  `async def beat(worker_id: str, *, mode: str, poll_seconds: float, lease_seconds: int, max_concurrent_goals: int, inflight_goals: int) -> None`
  and `def list_workers() -> list[WorkerRow]`.
- `WorkerRow` is a frozen dataclass: `worker_id, mode, started_at, last_seen_at, poll_seconds, lease_seconds, max_concurrent_goals, inflight_goals` (datetimes as `datetime`).

- [ ] **Step 1: Write the failing test**

`backend/tests/integration/test_worker_registry.py` covering: a first `beat` inserts a row with `started_at == last_seen_at`; a second `beat` from the same `worker_id` **updates in place** (one row, `started_at` preserved, `last_seen_at` advanced, `inflight_goals` updated); two different ids produce two rows; and a write against a closed engine is swallowed (returns None, logs, does not raise).

- [ ] **Step 2: Run it — expect ImportError**

`cd backend && timeout 300 uv run pytest tests/integration/test_worker_registry.py -q --no-cov`

- [ ] **Step 3: Add the ORM table**

Append to `backend/src/infra/db/tables.py`, following the file's existing `Mapped`/`mapped_column` style and ISO-string datetime convention:

```python
class WorkerTable(Base):
    """A worker process reporting that it is alive.

    NOT plan-scoped: a worker outlives every plan, so no FK and no cascade.
    Upserted by worker_id rather than appended, so a restarted worker replaces
    its own row and the table stays the size of the fleet.
    """

    __tablename__ = "workers"

    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String, nullable=False)
    poll_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    lease_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrent_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    inflight_goals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

Add `Float` to the SQLAlchemy imports if absent.

- [ ] **Step 4: Write the migration**

`backend/alembic/versions/0016_worker_registry.py`, `down_revision = "0015_plan_delete_cascade"`. Docstring explains WHY the table is not plan-scoped and why rows are upserted. `upgrade()` is one `op.create_table`; `downgrade()` is `op.drop_table("workers")`.

- [ ] **Step 5: Write the registry**

`backend/src/infra/db/worker_registry.py`, mirroring `src/infra/db/agent_event_sink.py` (read it first): `run_in_session` for the write, `await asyncio.to_thread(...)`, `except Exception: log.warning("worker_registry.beat_failed", ..., exc_info=True)`. The upsert is one statement:

```python
_BEAT_SQL = text(
    """
    INSERT INTO workers (worker_id, mode, started_at, last_seen_at, poll_seconds,
                         lease_seconds, max_concurrent_goals, inflight_goals)
    VALUES (:worker_id, :mode, :now, :now, :poll_seconds,
            :lease_seconds, :max_concurrent_goals, :inflight_goals)
    ON CONFLICT(worker_id) DO UPDATE SET
        mode = excluded.mode,
        last_seen_at = excluded.last_seen_at,
        poll_seconds = excluded.poll_seconds,
        lease_seconds = excluded.lease_seconds,
        max_concurrent_goals = excluded.max_concurrent_goals,
        inflight_goals = excluded.inflight_goals
    """
)
```

`started_at` is deliberately absent from the UPDATE branch: a restart keeps the original boot time visible only if the row is replaced, so document in a comment that a restart *does* keep the old `started_at` and why that is acceptable (the operator cares about `last_seen_at`; if the process identity changed, the id would differ).

- [ ] **Step 6: Green, then commit**

Run Step 2's command; then `timeout 300 uv run pytest tests/integration/test_migrations.py -q --no-cov`.

```bash
git add backend/alembic backend/src/infra/db backend/tests/integration/test_worker_registry.py
git commit -m "feat(worker): record worker liveness in its own table"
```

---

### Task 2: The heartbeat runs even while a goal is running

**Files:**
- Modify: `backend/src/infra/worker/main.py`
- Modify: `backend/src/infra/container.py` (cached `worker_registry` property)
- Test: `backend/tests/integration/test_worker_pool.py` (append)

**Interfaces:**
- Consumes: `WorkerRegistry.beat` (Task 1).
- Produces: a `_HEARTBEAT_SECONDS = 5.0` module constant and a background heartbeat task started before the main loop and cancelled during the drain.

- [ ] **Step 1: Write the failing test**

The regression this task exists for: **a worker with a goal in flight keeps beating.** Drive the loop with a goal that blocks for longer than one heartbeat interval, monkeypatch `_HEARTBEAT_SECONDS` to something small (e.g. `0.05`), and assert `last_seen_at` advances **while** the goal is still running. A test that only checks an idle worker beats would pass against the broken design this task avoids.

- [ ] **Step 2: Run it — expect failure**

- [ ] **Step 3: Add the container property**

In `backend/src/infra/container.py`, beside the other cached adapters:

```python
@cached_property
def worker_registry(self) -> WorkerRegistry:
    return WorkerRegistry(self.session_factory)
```

- [ ] **Step 4: Start the heartbeat as its own task**

In `backend/src/infra/worker/main.py`, after `log.info("worker.started", ...)` and before the `while` loop:

```python
async def _heartbeat() -> None:
    """Its own task, not a loop-body call: the main loop blocks in
    asyncio.wait(FIRST_COMPLETED) for the whole of a long goal, so a heartbeat
    written from the loop body would report a BUSY worker as dead."""
    while stop is None or not stop.is_set():
        await container.worker_registry.beat(
            worker_id,
            mode=runner_mode.mode,
            poll_seconds=poll_seconds,
            lease_seconds=lease_seconds,
            max_concurrent_goals=max_concurrent_goals,
            inflight_goals=len(inflight),
        )
        await asyncio.sleep(_HEARTBEAT_SECONDS)

heartbeat = asyncio.ensure_future(_heartbeat())
```

and in the drain after the loop, before `log.info("worker.stopped", ...)`:

```python
heartbeat.cancel()
with suppress(asyncio.CancelledError):
    await heartbeat
```

Import `suppress` from `contextlib`.

- [ ] **Step 5: Green, then commit**

`timeout 300 uv run pytest tests/integration/test_worker_pool.py -q --no-cov`

```bash
git commit -m "feat(worker): heartbeat from its own task, so a busy worker is not reported dead"
```

---

### Task 3: `GET /api/workers` and the readiness check

**Files:**
- Create: `backend/src/api/routers/workers.py`
- Modify: `backend/src/api/server.py` (mount, using the existing `_guarded` list)
- Modify: `backend/src/api/routers/readiness.py` (a `workers` check)
- Test: `backend/tests/integration/test_workers_api.py` (create)

**Interfaces:**
- Produces: `GET /api/workers` → `list[WorkerStatusResponse]` with the row's fields plus `stale: bool` and `seconds_since_seen: float`.
- `STALE_AFTER_SECONDS = 15.0` (3 missed beats) exported from `routers/workers.py` and imported by `readiness.py` — one definition.

- [ ] **Step 1: Write the failing tests**

Cover: an empty table returns `[]`; a fresh beat returns `stale is False`; a row whose `last_seen_at` is older than `STALE_AFTER_SECONDS` returns `stale is True` with a positive `seconds_since_seen`, **computed against the container clock, not wall time**; and the readiness `workers` check is `fail` with no rows, `warn` when every row is stale, `ok` with a fresh one.

- [ ] **Step 2: Run — expect 404**

- [ ] **Step 3: Write the router**

Server-computed staleness, for the same reason `worker_lease.expired` is server-computed (the client's clock is not the server's). Use `container.clock.now()`, never `datetime.now()` directly, so tests can inject.

- [ ] **Step 4: Add the readiness check**

In `readiness.py`, a `_workers(container)` check: `fail` / "no worker has reported — start one with `orchestrate worker start`" when the list is empty; `warn` / "N worker(s) last seen …s ago" when all are stale; `ok` otherwise. `fail` for never-started because that is the setup mistake; `warn` for gone-quiet because a restart is normal.

- [ ] **Step 5: Mount it**

In `server.py`: `app.include_router(workers.router, prefix=_prefix, dependencies=_guarded)`.

- [ ] **Step 6: Green, then commit**

Run the new test **and** `tests/integration/test_control_plane_auth.py` (it picks up the new operation automatically and must stay green) and `tests/integration/test_readiness.py`.

---

### Task 4: `requires_human` on every served block

**Files:**
- Modify: `backend/src/api/routers/plans.py`
- Test: `backend/tests/integration/test_block_report.py` (append)

**Interfaces:**
- Produces: `BlockResponse` (all `PlanBlock` fields + `requires_human: bool`) and `_block_response(block) -> BlockResponse`; `PlanDetailResponse.block` and `.goal_blocks` change type to it.

- [ ] **Step 1: Write the failing test**

Assert a plan-wide block and each `goal_blocks` entry carry `requires_human`, and that the value matches `block_policy.requires_human(kind)` **for every kind in `_POLICIES`** — iterate the policy table rather than hard-coding kinds, so a new kind cannot ship unserved.

- [ ] **Step 2: Run — expect KeyError on the new field**

- [ ] **Step 3: Add the projection**

In `plans.py`, `BlockResponse` exactly as §3.4 of the design lists it, plus:

```python
def _block_response(block: PlanBlock) -> BlockResponse:
    """The domain block plus the one fact only block_policy knows.

    `requires_human` is a pure function of `kind`, so it is projected here
    rather than persisted: a policy change must not leave stale copies in old
    rows, and adding a field to the FROZEN PlanBlock would need an un-freeze
    for a value the block can already derive.
    """
    return BlockResponse(**block.model_dump(), requires_human=requires_human(block.kind))
```

Import `requires_human` from `src.app.block_policy`. If `model_dump()` carries fields `BlockResponse` does not declare, list the fields explicitly instead of `**` — do not add a catch-all.

- [ ] **Step 4: Green, then commit**

---

### Task 5: `action_endpoints`, and the contract test

**Files:**
- Modify: `backend/src/api/routers/plans.py`
- Test: `backend/tests/integration/test_legal_actions_contract.py` (create)

**Interfaces:**
- Produces: `action_endpoints_for(plan) -> dict[str, str]` and `PlanDetailResponse.action_endpoints`.

- [ ] **Step 1: Write the failing tests**

Two properties, both from served data:

```python
def test_every_advertised_action_names_a_real_operation(client, plan_in_state):
    plan = client.get(f"/api/plans/{plan_id}").json()
    assert set(plan["legal_actions"]) <= set(plan["action_endpoints"]), (
        "an action was advertised with no endpoint — legal_actions publishes raw "
        "strings, so a client has no way to act on one that is missing here"
    )
    served = {f"{m} {p}" for p, ops in create_app().openapi()["paths"].items() for m in ops}
    for route in plan["action_endpoints"].values():
        assert route.upper().split()[0] + " " + route.split()[1] in served
```

and, for each state, call every advertised action through its served endpoint and assert the status is not 404, 405 or 422. A 409 is legitimate (a concurrent claim) and passes.

Drive at least: IDLE (`start_intent`), the intent gate, the cycle-draft gate, the publication gate (`review:*`), RUNNING (`pause`, `start_replan`), PAUSED (`resume`, `edit_pending_work`), and a blocked plan (block resolutions). Reuse the state helpers already in `tests/integration/test_api.py` — read them first rather than rebuilding.

- [ ] **Step 2: Run — expect KeyError `action_endpoints`**

- [ ] **Step 3: Build the map**

```python
_ACTION_ROUTES: dict[str, str] = {
    "pause": "POST /api/plans/{plan_id}/pause",
    "resume": "POST /api/plans/{plan_id}/resume",
    "start_replan": "POST /api/plans/{plan_id}/replan",
    "start_intent": "POST /api/plans/{plan_id}/intent",
    "edit_pending_work": "POST /api/plans/{plan_id}/edits",
    "edit_task": "POST /api/plans/{plan_id}/edits",
    "retry_stage": "POST /api/plans/{plan_id}/retry-stage",
    "wait_and_retry": "POST /api/plans/{plan_id}/retry",
    "bind_project": "POST /api/plans/{plan_id}/project-binding",
}

_GATE_ROUTES: dict[str, str] = {
    "intent": "POST /api/plans/{plan_id}/intent/approve",
    "cycle_draft": "POST /api/plans/{plan_id}/cycle-draft/approve",
    "cycle_completion": "POST /api/plans/{plan_id}/publication",
}
```

`action_endpoints_for(plan)` maps each entry of `plan.legal_actions`: a
`review:<decision>` resolves through `_GATE_ROUTES[plan.review_gate.subject_type.value]`
— the part a client cannot derive, because only the server knows which gate is
open. An action with no known route is **omitted, never guessed**; Step 1's
first assertion is what makes that omission visible.

Keep `{plan_id}` as a template: it stays comparable to `app.openapi()["paths"]`, which is what lets the test verify the map instead of trusting it.

- [ ] **Step 4: Green, then commit**

---

### Task 6: Close out

- [ ] **Step 1:** `cd frontend && npm run generate:api && npx tsc --noEmit && npm run build`. Add `requires_human` to `PlanBlock` and `action_endpoints` to `Plan` in `frontend/src/types/ui.ts` — `backend/tests/unit/test_plan_read_model_parity.py` fails until `action_endpoints` is declared.
- [ ] **Step 2:** In `docs/architecture/capability-matrix.md`: delete the G10 section, change the "Worker liveness" row to `api-only` with `GET /api/workers`, add rows for the new operation, and change the "Is this block mine or the orchestrator's?" row from `hidden` to served. The matrix completeness test fails until the new route is listed — that is the test working.
- [ ] **Step 3:** Full verification:

```bash
cd backend && uv run ruff check src tests && uv run mypy src \
  && timeout 400 uv run pytest -m "not integration" -q --no-cov \
  && timeout 600 uv run pytest -m integration -q --no-cov
cd ../frontend && npx tsc --noEmit && npm run build
```

- [ ] **Step 4:** Commit, push, open the PR.

## Self-review

**Spec coverage.** §3.1 → Task 1. §3.2 → Task 2. §3.3 → Task 3. §3.4 → Task 4. §3.5 → Task 5. §3.6 → Task 5 Step 1. §3.7 needs no code by design and is recorded in the spec. §4 → Task 1 Steps 3-4. §5's table maps one row per task.

**Known soft spots, called out rather than hidden.** Task 2 Step 1 states explicitly that an idle-worker test would pass against the broken design, because that is the trap. Task 4 Step 3 says what to do if `model_dump()` and `BlockResponse` disagree instead of assuming they match. Task 5 Step 1 says to read the existing state helpers first rather than rebuilding them.

**Type consistency.** `WorkerRegistry.beat`, `WorkerRow`, `STALE_AFTER_SECONDS`, `BlockResponse`/`_block_response`, `action_endpoints_for` are named identically everywhere they appear.
