# Phase 4.3 — Evidence Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve one evidence read model per cycle — accepted evidence, protected scope, promoted git refs and the recorded disposition — closing capability-matrix gap G9 and Phase 4.

**Architecture:** Three of the four facts already exist in the domain and only need a read surface. The fourth (promoted refs) is half-recorded as an unattributed `git:<sha>` string, so a new plan-scoped `goal_promotions` table captures it properly, written through a fifth UnitOfWork repository inside the transaction that already records the goal completing. A new `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` projects all four. No domain change anywhere.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core (`text()` SQL, not the ORM query layer), Alembic, SQLite (WAL), pytest, structlog, Pydantic v2.

## Global Constraints

- **Approved spec:** `docs/superpowers/specs/2026-07-28-phase-4-3-evidence-truth-design.md`. Read it before Task 1.
- **No domain change.** Nothing under `src/domain/` is modified. No `docs/decisions/decision-log.md` entry — deliberately.
- **Dependency rule:** `domain -> app -> infra & api`. `src/app/` must never import `src/infra/`. Infra importing app is fine and is what makes Task 1 legal.
- **Type checking:** `mypy src` must pass with **zero errors and no new excludes**.
- **Lint:** `ruff check src tests --fix` clean.
- **Every file starts with** `from __future__ import annotations`.
- **No `print()` and no stdlib `logging`.** Use `structlog.get_logger(__name__)` with namespaced event names.
- **No `HTTPException` in routers.** There are currently zero in the codebase. Every non-2xx goes through an error class with a stable `code`, mapped once in `src/api/exceptions.py::_STATUS_BY_CODE`.
- **Migration chain must stay one linear head.** Verify with `alembic heads` after Task 2.
- **All backend commands run from `backend/`.**
- Test commands: `pytest -m "not integration"` (fast), `pytest -m integration`.

---

### Task 1: One home for the branch-naming convention

The names `cycle/<id>`, `goal/<id>` and `task/<id>/<run_id>` are currently built by f-string in **two** places inside `workspace.py`, and `CLAUDE.md` documents a third, different shape. Task 4 needs a fourth reader. This task gives the convention one definition before anything else consumes it.

**Files:**
- Create: `backend/src/app/branch_names.py`
- Create: `backend/tests/unit/test_branch_names.py`
- Modify: `backend/src/infra/git/workspace.py:204-216` and `:281-283`

**Interfaces:**
- Consumes: nothing.
- Produces: `cycle_branch(cycle_id: str) -> str`, `goal_branch(goal_id: str) -> str`, `task_branch(task_id: str, run_id: str) -> str`, `legacy_plan_branch(plan_id: str) -> str`, `legacy_task_branch(task_id: str, attempt: int) -> str`. Tasks 4 and 9 depend on these exact names.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_branch_names.py`:

```python
from __future__ import annotations

from src.app.branch_names import (
    cycle_branch,
    goal_branch,
    legacy_plan_branch,
    legacy_task_branch,
    task_branch,
)


def test_cyclic_ladder_names() -> None:
    assert cycle_branch("c1") == "cycle/c1"
    assert goal_branch("g1") == "goal/g1"
    assert task_branch("t1", "r1") == "task/t1/r1"


def test_legacy_names_stay_separate_from_the_cyclic_ladder() -> None:
    # A cyclic plan has NO plan-level branch: the cycle branch is cut straight
    # from the repository default. These two exist only for pre-cyclic rows.
    assert legacy_plan_branch("p1") == "plan/p1"
    assert legacy_task_branch("t1", 2) == "task/t1/a2"


def test_cyclic_task_branch_keys_on_run_not_attempt() -> None:
    # A run id is globally unique, so a retry never collides with a branch a
    # previous attempt left behind. This is the difference the docs got wrong.
    assert task_branch("t1", "run-abc") != legacy_task_branch("t1", 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_branch_names.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.app.branch_names'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/app/branch_names.py`:

```python
"""The git branch ladder, in one place.

`infra/git/workspace.py` built these names in two places — `begin` and
`_merge_goal_sync` — and CLAUDE.md described a third, different shape. That is
how the documented ladder drifted from the one the adapter actually creates:
the local variable holding the goal branch in `begin` is still named
`plan_branch`. Recording promoted refs adds a fourth reader (the execution
handler), so the convention gets one definition instead of a fourth copy.

`src/app` is the only layer that can host it. The dependency rule is
`domain -> app -> infra & api`, so infra may import app but never the reverse,
and the domain is frozen.
"""

from __future__ import annotations


def cycle_branch(cycle_id: str) -> str:
    """Cut from the repository's detected default branch on first use."""
    return f"cycle/{cycle_id}"


def goal_branch(goal_id: str) -> str:
    """Cut from the cycle branch; merged back into it on promotion."""
    return f"goal/{goal_id}"


def task_branch(task_id: str, run_id: str) -> str:
    """One branch per RUN. A run id is globally unique, so a retry never reuses
    the branch a previous attempt left behind."""
    return f"task/{task_id}/{run_id}"


def legacy_plan_branch(plan_id: str) -> str:
    """Pre-cyclic plans only. A cyclic plan has no plan-level branch."""
    return f"plan/{plan_id}"


def legacy_task_branch(task_id: str, attempt: int) -> str:
    """Pre-cyclic plans only."""
    return f"task/{task_id}/a{attempt}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_branch_names.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Adopt the module in the git adapter**

In `backend/src/infra/git/workspace.py`, add to the imports:

```python
from src.app.branch_names import (
    cycle_branch,
    goal_branch,
    legacy_plan_branch,
    legacy_task_branch,
    task_branch,
)
```

Replace the body at `:204-216`. Note `plan_branch` keeps its variable name only because the rest of the method uses it; the comment now says what it actually holds:

```python
        if cycle_id is not None and goal_id is not None and run_id is not None:
            cycle_branch_name = cycle_branch(cycle_id)
            # Named `plan_branch` for the shared code below, but on the cyclic
            # ladder this rung IS the goal branch — there is no plan branch.
            plan_branch = goal_branch(goal_id)
            task_branch_name = task_branch(task_id, run_id)
            if not _git_ok(self._repo, "rev-parse", "--verify", cycle_branch_name):
                _git(self._repo, "branch", cycle_branch_name, self._default_branch)
            if not _git_ok(self._repo, "rev-parse", "--verify", plan_branch):
                _git(self._repo, "branch", plan_branch, cycle_branch_name)
        else:
            plan_branch = legacy_plan_branch(plan_id)
            task_branch_name = legacy_task_branch(task_id, attempt)
            if not _git_ok(self._repo, "rev-parse", "--verify", plan_branch):
                _git(self._repo, "branch", plan_branch, self._default_branch)
                log.info("workspace.plan_branch_created", branch=plan_branch)
```

Then rename the local `task_branch` variable to `task_branch_name` throughout the rest of `begin` (it would otherwise shadow the imported function). Read the whole method and update every reference.

Replace `:282-283` in `_merge_goal_sync`:

```python
    def _merge_goal_sync(self, cycle_id: str, goal_id: str) -> str:
        cycle_branch_name = cycle_branch(cycle_id)
        goal_branch_name = goal_branch(goal_id)
```

Then rename the locals `cycle_branch` / `goal_branch` to `cycle_branch_name` / `goal_branch_name` throughout the rest of `_merge_goal_sync` for the same shadowing reason.

- [ ] **Step 6: Verify nothing regressed**

Run: `pytest tests/integration/test_git_workspace.py -v && mypy src && ruff check src tests`
Expected: all PASS, mypy zero errors. The workspace tests assert real branch names against a real repo, so they are the proof the refactor is behaviour-preserving.

- [ ] **Step 7: Commit**

```bash
git add backend/src/app/branch_names.py backend/tests/unit/test_branch_names.py backend/src/infra/git/workspace.py
git commit -m "refactor(git): give the branch ladder one definition

workspace.py built cycle/<id> and goal/<id> in two places and CLAUDE.md
documented a third shape. Recording promoted refs would have added a
fourth. src/app/branch_names.py is now the single definition; infra
imports it, which the dependency rule permits."
```

---

### Task 2: The `goal_promotions` table

**Files:**
- Create: `backend/alembic/versions/0017_goal_promotions.py`
- Modify: `backend/src/infra/db/tables.py` (append the table class)
- Modify: `backend/tests/integration/test_delete_plan_leaves_nothing.py:37-44` (add to `PLAN_SCOPED`)

**Interfaces:**
- Consumes: nothing.
- Produces: table `goal_promotions` with columns `id, plan_id, cycle_id, goal_id, from_ref, into_ref, merge_sha, promoted_at`, and the ORM class `GoalPromotionTable`. Task 3 reads and writes it.

- [ ] **Step 1: Write the failing test**

Add `"goal_promotions"` to the `PLAN_SCOPED` tuple in `backend/tests/integration/test_delete_plan_leaves_nothing.py`:

```python
PLAN_SCOPED = (
    *FORMERLY_LEAKING,
    "goal_leases",
    "execution_runs",
    "execution_attempts",
    "planning_operations",
    "planning_artifacts",
    "goal_promotions",
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_delete_plan_leaves_nothing.py -v`
Expected: FAIL — the table does not exist yet (`no such table: goal_promotions`).

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/0017_goal_promotions.py`:

```python
"""Record where a promoted goal's work actually landed.

The goal->cycle merge SHA was already persisted before this migration, but only
as an untyped string: `ExecutionHandler._promote_goal` appended `git:<sha>` to
`Cycle.evidence_refs`, a `list[str]` with no goal attribution, no branch name
and no timestamp. It answers "something was merged" and cannot answer "which
goal landed where" — which is the half of operator job J7 about where the code
went.

Branch names are stored as the adapter ACTUALLY built them rather than being
reconstructed at read time. Reconstruction is not safe here: the cyclic ladder
creates no `plan/<plan_id>` rung at all and keys task branches on run id, so a
read model deriving refs from the previously documented convention would have
advertised branches that are never created.

Plan-scoped, so it opts into migration 0015's cascade rule: deleting a plan must
leave nothing behind. `test_delete_plan_leaves_nothing.py` enforces this both by
name and by a schema-drift guard that fails any table carrying `plan_id`
without ON DELETE CASCADE.

No backfill. Existing `Cycle.evidence_refs` entries cannot be attributed to
goals except by promotion order, which would be a guess; cycles promoted before
this migration are served through the read model's
`unattributed_evidence_refs` field instead.

Revision ID: 0017_goal_promotions
Revises: 0016_worker_registry
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_goal_promotions"
down_revision = "0016_worker_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_promotions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cycle_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        # The branches the workspace adapter merged, stored verbatim.
        sa.Column("from_ref", sa.String(), nullable=False),
        sa.Column("into_ref", sa.String(), nullable=False),
        sa.Column("merge_sha", sa.String(), nullable=False),
        sa.Column("promoted_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_goal_promotions_cycle",
        "goal_promotions",
        ["plan_id", "cycle_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_goal_promotions_cycle", table_name="goal_promotions")
    op.drop_table("goal_promotions")
```

- [ ] **Step 4: Declare the table in the ORM metadata**

Tables live in **both** places: alembic writes the schema, and `tables.py` is what `Base.metadata.create_all()` builds in tests. Append to `backend/src/infra/db/tables.py`:

```python
class GoalPromotionTable(Base):
    __tablename__ = "goal_promotions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    cycle_id: Mapped[str] = mapped_column(String, nullable=False)
    goal_id: Mapped[str] = mapped_column(String, nullable=False)
    from_ref: Mapped[str] = mapped_column(String, nullable=False)
    into_ref: Mapped[str] = mapped_column(String, nullable=False)
    merge_sha: Mapped[str] = mapped_column(String, nullable=False)
    promoted_at: Mapped[str] = mapped_column(String, nullable=False)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
pytest tests/integration/test_delete_plan_leaves_nothing.py tests/integration/test_migrations.py -v
alembic heads
```
Expected: tests PASS; `alembic heads` prints exactly one head, `0017_goal_promotions`.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0017_goal_promotions.py backend/src/infra/db/tables.py backend/tests/integration/test_delete_plan_leaves_nothing.py
git commit -m "feat(db): goal_promotions table (migration 0017)

Plan-scoped with ON DELETE CASCADE. Stores the branches the workspace
adapter actually merged rather than reconstructing them, because the
cyclic ladder creates no plan/<plan_id> rung and keys task branches on
run id. No backfill."
```

---

### Task 3: The promotion record, its port, and both adapters

**Files:**
- Create: `backend/src/app/promotion_records.py`
- Create: `backend/src/infra/db/goal_promotion_repository.py`
- Create: `backend/src/app/testing/promotion_records.py`
- Create: `backend/tests/integration/test_goal_promotion_repository.py`
- Modify: `backend/src/app/ports.py` (re-export + UoW property)
- Modify: `backend/src/infra/db/unit_of_work.py:25-56`
- Modify: `backend/src/app/testing/fakes.py:277-305`

**Interfaces:**
- Consumes: `goal_promotions` table (Task 2).
- Produces: `GoalPromotion` frozen dataclass with fields `id, plan_id, cycle_id, goal_id, from_ref, into_ref, merge_sha, promoted_at: datetime`; `GoalPromotionRepository` Protocol with `add(promotion: GoalPromotion) -> None` and `list_for_cycle(plan_id: str, cycle_id: str) -> list[GoalPromotion]`; and `uow.promotions` on both UnitOfWork implementations. Tasks 4 and 5 depend on all three.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_goal_promotion_repository.py`:

```python
"""The promotion ledger is transactional, not best-effort.

An evidence record that can silently go missing makes the evidence read model
under-report where code went, so these tests pin the rollback semantics that
the in-memory fake must match exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.app.promotion_records import GoalPromotion

pytestmark = pytest.mark.integration


def _promotion(promotion_id: str = "pr1", goal_id: str = "g1") -> GoalPromotion:
    return GoalPromotion(
        id=promotion_id,
        plan_id="p1",
        cycle_id="c1",
        goal_id=goal_id,
        from_ref="goal/g1",
        into_ref="cycle/c1",
        merge_sha="a1b2c3d",
        promoted_at=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
    )


def test_committed_promotion_is_readable(promotion_env) -> None:
    uow = promotion_env.uow
    with uow:
        uow.promotions.add(_promotion())
    with uow:
        found = uow.promotions.list_for_cycle("p1", "c1")
    assert [item.id for item in found] == ["pr1"]
    assert found[0].from_ref == "goal/g1"
    assert found[0].into_ref == "cycle/c1"
    assert found[0].merge_sha == "a1b2c3d"


def test_rollback_discards_the_promotion(promotion_env) -> None:
    uow = promotion_env.uow
    with pytest.raises(RuntimeError):
        with uow:
            uow.promotions.add(_promotion())
            raise RuntimeError("boom")
    with uow:
        assert uow.promotions.list_for_cycle("p1", "c1") == []


def test_promotions_are_scoped_to_their_cycle(promotion_env) -> None:
    uow = promotion_env.uow
    with uow:
        uow.promotions.add(_promotion())
    with uow:
        assert uow.promotions.list_for_cycle("p1", "other-cycle") == []
        assert uow.promotions.list_for_cycle("other-plan", "c1") == []
```

Add the `promotion_env` fixture to this file, parametrized over both backends so fake and real semantics are proven identical:

```python
@pytest.fixture(params=["fakes", "sqlite"])
def promotion_env(request, tmp_path):
    """Both backends, because the truth test's value is that they behave the
    same. Mirrors the env_factory pattern in tests/support.py."""
    from tests.support import build_promotion_env

    return build_promotion_env(request.param, tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_goal_promotion_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.app.promotion_records'`

- [ ] **Step 3: Write the record and the port**

Create `backend/src/app/promotion_records.py`:

```python
"""Where a promoted goal's work landed.

Operational application state, like the run/attempt ledger in
`execution_records.py`: not a domain aggregate and not telemetry. It records the
branches the workspace adapter ACTUALLY merged and the SHA the merge produced,
so "where did this goal's code go" is answerable without reconstructing a name
from a convention.

Kept out of `execution_records.py` deliberately: that module's repository
protocol already spans runs, attempts, planning operations and runtime circuits,
and a fifth concern would make it harder to hold in context, not easier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GoalPromotion:
    id: str
    plan_id: str
    cycle_id: str
    goal_id: str
    # Stored as the adapter built them, never re-derived at read time.
    from_ref: str
    into_ref: str
    merge_sha: str
    promoted_at: datetime


@runtime_checkable
class GoalPromotionRepository(Protocol):
    """Transactional repository bound to the application UnitOfWork.

    Deliberately NOT the best-effort pattern used by `workers`/`agent_events`:
    those are telemetry, this is evidence.
    """

    def add(self, promotion: GoalPromotion) -> None: ...

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]: ...
```

- [ ] **Step 4: Write the SQLite adapter**

Create `backend/src/infra/db/goal_promotion_repository.py`:

```python
"""SQLite adapter for the transactional goal-promotion ledger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.app.promotion_records import GoalPromotion

_COLUMNS = "id, plan_id, cycle_id, goal_id, from_ref, into_ref, merge_sha, promoted_at"


def _promotion(row: object) -> GoalPromotion:
    values = row  # Row supports positional access; keep ORM types out of the port.
    return GoalPromotion(
        id=str(values[0]),  # type: ignore[index]
        plan_id=str(values[1]),  # type: ignore[index]
        cycle_id=str(values[2]),  # type: ignore[index]
        goal_id=str(values[3]),  # type: ignore[index]
        from_ref=str(values[4]),  # type: ignore[index]
        into_ref=str(values[5]),  # type: ignore[index]
        merge_sha=str(values[6]),  # type: ignore[index]
        promoted_at=datetime.fromisoformat(str(values[7])),  # type: ignore[index]
    )


class SqliteGoalPromotionRepository:
    """Bound to the live UoW session; every write shares the Plan/outbox txn.

    Sharing the transaction is also what keeps this off the write-lock critical
    path: SQLite in WAL mode admits exactly one writer, so an INSERT inside the
    finalize transaction acquires no new lock, where a separate connection would
    become a second contender against the hottest write in the system.
    """

    def __init__(self) -> None:
        self._session: Session | None = None

    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def _bound(self) -> Session:
        if self._session is None:
            raise RuntimeError(
                "SqliteGoalPromotionRepository used outside a UnitOfWork transaction"
            )
        return self._session

    def add(self, promotion: GoalPromotion) -> None:
        self._bound().execute(
            text(
                f"INSERT INTO goal_promotions ({_COLUMNS}) VALUES "
                "(:id, :plan_id, :cycle_id, :goal_id, :from_ref, :into_ref, "
                ":merge_sha, :promoted_at)"
            ),
            {
                "id": promotion.id,
                "plan_id": promotion.plan_id,
                "cycle_id": promotion.cycle_id,
                "goal_id": promotion.goal_id,
                "from_ref": promotion.from_ref,
                "into_ref": promotion.into_ref,
                "merge_sha": promotion.merge_sha,
                "promoted_at": promotion.promoted_at.isoformat(),
            },
        )

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_COLUMNS} FROM goal_promotions "
                    "WHERE plan_id = :plan_id AND cycle_id = :cycle_id "
                    "ORDER BY promoted_at, id"
                ),
                {"plan_id": plan_id, "cycle_id": cycle_id},
            )
            .all()
        )
        return [_promotion(row) for row in rows]
```

- [ ] **Step 5: Write the in-memory twin**

Create `backend/src/app/testing/promotion_records.py`:

```python
"""In-memory GoalPromotionRepository with the SQLite adapter's transaction
semantics: staged writes are visible inside the with-block and discarded on
rollback. The truth test only proves something if both backends agree."""

from __future__ import annotations

from src.app.promotion_records import GoalPromotion


class InMemoryGoalPromotionRepository:
    def __init__(self) -> None:
        self._committed: list[GoalPromotion] = []
        self._staged: list[GoalPromotion] = []

    def _begin(self) -> None:
        self._staged = []

    def _commit(self) -> None:
        self._committed.extend(self._staged)
        self._staged = []

    def _rollback(self) -> None:
        self._staged = []

    def add(self, promotion: GoalPromotion) -> None:
        self._staged.append(promotion)

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]:
        return [
            item
            for item in (*self._committed, *self._staged)
            if item.plan_id == plan_id and item.cycle_id == cycle_id
        ]
```

- [ ] **Step 6: Wire both UnitOfWork implementations**

In `backend/src/app/ports.py`, add the import and re-export next to the existing `ExecutionRecordRepository` ones (line ~20 and the `__all__` block ~line 48):

```python
from src.app.promotion_records import GoalPromotion, GoalPromotionRepository
```

Add `"GoalPromotion"` and `"GoalPromotionRepository"` to `__all__`, and add the property to the `UnitOfWork` Protocol (after `goal_leases`, ~line 364):

```python
    @property
    def promotions(self) -> GoalPromotionRepository: ...
```

In `backend/src/infra/db/unit_of_work.py`, add the import and three lines:

```python
from src.infra.db.goal_promotion_repository import SqliteGoalPromotionRepository
```

```python
        self.outbox = SqliteOutbox()
        self.promotions = SqliteGoalPromotionRepository()
```

```python
        self.outbox.bind(self._session)
        self.promotions.bind(self._session)
```

```python
            self.outbox.unbind()
            self.promotions.unbind()
```

In `backend/src/app/testing/fakes.py`, update `InMemoryUnitOfWork` (`:277-305`):

```python
    def __init__(
        self,
        repo: InMemoryPlanRepository,
        outbox: InMemoryOutbox,
        executions: InMemoryExecutionRecordRepository | None = None,
        goal_leases: InMemoryGoalLeaseRepository | None = None,
        promotions: InMemoryGoalPromotionRepository | None = None,
    ) -> None:
        self.plans = repo
        self.goal_leases = goal_leases or InMemoryGoalLeaseRepository()
        self.outbox = outbox
        self.executions = executions or InMemoryExecutionRecordRepository()
        self.promotions = promotions or InMemoryGoalPromotionRepository()

    def __enter__(self) -> "InMemoryUnitOfWork":
        self.executions._begin()
        self.promotions._begin()
        return self

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
            self.executions._commit()
            self.outbox._commit()
            self.promotions._commit()
        else:
            self.executions._rollback()
            self.outbox._rollback()
            self.promotions._rollback()
```

with the import `from src.app.testing.promotion_records import InMemoryGoalPromotionRepository`.

- [ ] **Step 7: Add the test helper**

In `backend/tests/support.py`, add alongside the existing `env_factory` machinery:

```python
@dataclass
class PromotionEnv:
    """Just a UnitOfWork — the promotion ledger needs no plan, runner or clock."""

    uow: UnitOfWork


def build_promotion_env(backend: str, tmp_path: Path) -> PromotionEnv:
    if backend == "fakes":
        return PromotionEnv(
            uow=InMemoryUnitOfWork(InMemoryPlanRepository(), InMemoryOutbox())
        )
    engine = build_engine(tmp_path / "promotions.db")
    Base.metadata.create_all(engine)
    # The FK to `plans` is enforced, so the row this test writes needs a parent.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO plans (id, version, status, paused, pause_requested) "
                "VALUES ('p1', 1, 'running', 0, 0)"
            )
        )
    return PromotionEnv(
        uow=SqliteUnitOfWork(sessionmaker(bind=engine), FakeClock())
    )
```

If the `plans` table requires columns beyond those five, read its definition in `src/infra/db/tables.py` and add every `nullable=False` column without a default to the INSERT.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/integration/test_goal_promotion_repository.py -v && mypy src`
Expected: 6 PASS (3 tests × 2 backends), mypy zero errors.

- [ ] **Step 9: Commit**

```bash
git add backend/src/app/promotion_records.py backend/src/infra/db/goal_promotion_repository.py backend/src/app/testing/promotion_records.py backend/tests/integration/test_goal_promotion_repository.py backend/src/app/ports.py backend/src/infra/db/unit_of_work.py backend/src/app/testing/fakes.py backend/tests/support.py
git commit -m "feat(app): transactional goal-promotion ledger as a fifth UoW repo

Evidence, not telemetry: a record that can silently go missing makes the
evidence read model under-report. Sharing the plan transaction also
acquires no new SQLite write lock, where a separate connection would
contend with the finalize path."
```

---

### Task 4: Record the promotion where the merge is finalized

**Files:**
- Modify: `backend/src/app/handlers/execution_handler.py:1340-1372`
- Modify: `backend/tests/integration/test_default_cyclic_execution.py`

**Interfaces:**
- Consumes: `GoalPromotion`, `uow.promotions.add` (Task 3); `cycle_branch`, `goal_branch` (Task 1).
- Produces: one `goal_promotions` row per successfully promoted goal. Task 5 reads them.

- [ ] **Step 1: Write the failing assertions**

`backend/tests/integration/test_default_cyclic_execution.py` drives the canonical intent → draft → cycle → enrichment → execution → publication walk, but it has **no reusable fixture** — the walk lives inline in `test_shipped_stub_and_dry_run_execute_a_cycle_to_publication_gate(tmp_path, monkeypatch)` (`:64`). Do not invent a fixture here; Task 5 extracts one.

Add these assertions **inside that existing test**, after the cycle's goals have been promoted and before the publication step. Use the `uow`, `plan_id` and `cycle_id` variables the test already has in scope (read the test body and match its local names):

```python
    # G9: "where did the code go" must be answerable without reconstructing a
    # branch name from a convention the cyclic ladder does not follow.
    with uow:
        promotions = uow.promotions.list_for_cycle(plan_id, cycle_id)
        plan = uow.plans.get(plan_id)

    cycle = next(item for item in plan.cycles if item.id == cycle_id)
    promoted_goal_ids = [
        goal.id for goal in cycle.goals if goal.status.value == "done"
    ]
    assert [item.goal_id for item in promotions] == promoted_goal_ids

    for item in promotions:
        assert item.from_ref == f"goal/{item.goal_id}"
        assert item.into_ref == f"cycle/{cycle_id}"
        # The recorded refs must resolve in the REAL repo this walk built, so
        # the naming module and the git adapter cannot drift apart silently.
        _git(repo, "rev-parse", "--verify", item.from_ref)
        _git(repo, "rev-parse", "--verify", item.into_ref)
        _git(repo, "cat-file", "-e", item.merge_sha)
```

`_git` is already defined in this file at `:35`. Use whatever local variable the test holds the repository path in.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_default_cyclic_execution.py -v -k shipped_stub`
Expected: FAIL — `assert [] == ['g1', ...]`, because nothing writes the rows yet.

- [ ] **Step 3: Write the implementation**

In `backend/src/app/handlers/execution_handler.py`, add imports:

```python
from src.app.branch_names import cycle_branch, goal_branch
from src.app.promotion_records import GoalPromotion
```

Then in `_promote_goal`, immediately after `cycle.evidence_refs.append(f"git:{commit_sha}")` at `:1367`:

```python
            cycle.evidence_refs.append(f"git:{commit_sha}")
            # Recorded HERE, not at the merge call: everything above this line
            # in the transaction has already re-guarded the promotion
            # reservation, so a promotion that lost its reservation returned
            # PAUSED without leaving a phantom row. The refs come from the same
            # module the workspace adapter builds its branches from.
            uow.promotions.add(
                GoalPromotion(
                    id=new_id(),
                    plan_id=plan_id,
                    cycle_id=cycle_id,
                    goal_id=goal_id,
                    from_ref=goal_branch(goal_id),
                    into_ref=cycle_branch(cycle_id),
                    merge_sha=commit_sha,
                    promoted_at=self._clock.now(),
                )
            )
            plan.complete_goal(goal_id)
```

Leave `cycle.evidence_refs.append(...)` in place — removing it would change a persisted domain field's meaning for existing rows, and `:1194` reads that list into a block's evidence refs.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/integration/test_default_cyclic_execution.py -v
pytest -m "not integration"
mypy src
```
Expected: all PASS, mypy zero errors.

- [ ] **Step 5: Commit**

```bash
git add backend/src/app/handlers/execution_handler.py backend/tests/integration/test_default_cyclic_execution.py
git commit -m "feat(execution): record where each promoted goal's work landed

Written inside the finalize transaction, after the reservation re-guard,
so a lost reservation leaves no phantom row and a rollback takes the
promotion with it."
```

---

### Task 5: The evidence read model

**Files:**
- Create: `backend/src/api/routers/evidence.py`
- Create: `backend/tests/integration/cyclic_walk.py`
- Create: `backend/tests/integration/test_cycle_evidence_api.py`
- Modify: `backend/src/infra/errors.py` (add `CycleNotFoundError`)
- Modify: `backend/src/api/exceptions.py:38-46` (add `"CYCLE_NOT_FOUND": 404`)
- Modify: `backend/src/api/server.py:134-154` (register the router, guarded)
- Modify: `backend/tests/integration/test_default_cyclic_execution.py` (call the extracted helper)

**Interfaces:**
- Consumes: `uow.promotions.list_for_cycle` (Task 3).
- Produces: `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` returning `CycleEvidenceResponse`; and `drive_cycle_to_publication(...)` in `tests/integration/cyclic_walk.py`. Task 6 builds three fixtures on that helper; Task 8 consumes the endpoint from the fixture walkthrough.

- [ ] **Step 1: Extract the walk into a reusable helper**

The canonical walk is currently inline in `test_shipped_stub_and_dry_run_execute_a_cycle_to_publication_gate` (`test_default_cyclic_execution.py:64`), so nothing else can drive a completed cycle. Tasks 5 and 6 need four variations of it, so extract it once rather than copying it four times.

Create `backend/tests/integration/cyclic_walk.py` by **moving** the walk body out of that test:

```python
"""The canonical cyclic walk, callable.

Extracted from test_default_cyclic_execution.py so the evidence read model can
be asserted against a genuinely completed dry-run cycle rather than a hand-built
fixture that happens to look like one. Tier 0 only: stub reasoner, dry-run
runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient


@dataclass
class CompletedCycle:
    client: TestClient
    plan_id: str
    cycle_id: str
    repo: Path
    container: object  # AppContainer; typed loosely to avoid an infra import here


def drive_cycle_to_publication(
    tmp_path: Path,
    monkeypatch,
    *,
    disposition: str = "merge",
    output_reference: str | None = "cycle/merged",
    publish: bool = True,
) -> CompletedCycle:
    """Drive intent -> draft -> cycle -> enrichment -> execution -> publication."""
    ...
```

Fill the body by moving the existing test's code verbatim, parameterising only the publication step on `disposition` / `output_reference` / `publish`. Then rewrite `test_shipped_stub_and_dry_run_execute_a_cycle_to_publication_gate` to call `drive_cycle_to_publication(...)` and keep its own assertions — **including the promotion assertions added in Task 4**. Run it to confirm the extraction changed no behaviour:

Run: `pytest tests/integration/test_default_cyclic_execution.py -v`
Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/integration/test_cycle_evidence_api.py`:

```python
"""G9's objective test: one evidence read model per cycle, asserted against a
completed dry-run cycle."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_completed_cycle_serves_all_four_evidence_facts(evidence_client) -> None:
    client, plan_id, cycle_id = evidence_client

    response = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == plan_id
    assert body["cycle_id"] == cycle_id
    assert body["goals"], "a completed cycle has goals"

    goal = body["goals"][0]
    # 1. promoted refs
    assert goal["promotion"]["into_ref"] == f"cycle/{cycle_id}"
    assert goal["promotion"]["from_ref"] == f"goal/{goal['goal_id']}"
    assert goal["promotion"]["merge_sha"]

    task = goal["tasks"][0]
    # 2. protected scope, both halves joined
    assert "allowed_scope" in task["protected_scope"]
    assert "forbidden_scope" in task["protected_scope"]
    assert "protected_file_hashes" in task["protected_scope"]
    # 3. accepted evidence
    assert task["accepted_evidence"], "a done task has accepted evidence"
    assert all(item["exit_code"] == 0 for item in task["accepted_evidence"])
    # 4. disposition
    assert body["disposition"]["disposition"] in {
        "open_pr",
        "merge",
        "retain_branch",
        "discard",
    }


def test_unknown_cycle_is_404(evidence_client) -> None:
    client, plan_id, _ = evidence_client
    response = client.get(f"/api/plans/{plan_id}/cycles/nope/evidence")
    assert response.status_code == 404
```

Add the fixture to this file, built on the Step 1 helper:

```python
@pytest.fixture
def evidence_client(tmp_path, monkeypatch):
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(tmp_path, monkeypatch)
    return walk.client, walk.plan_id, walk.cycle_id
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_cycle_evidence_api.py -v`
Expected: FAIL — 404 on the evidence route, which is not registered yet.

- [ ] **Step 4: Add the not-found error**

In `backend/src/infra/errors.py`, after `AttemptNotFoundError`:

```python
class CycleNotFoundError(InfrastructureError):
    """No such cycle on this plan. Follows AttemptNotFoundError: a lookup miss
    for a non-domain identifier still travels as a coded error, never a router
    HTTPException — the status map in src/api/exceptions.py is the one table."""

    code = "CYCLE_NOT_FOUND"

    def __init__(self, plan_id: str, cycle_id: str) -> None:
        super().__init__(
            f"Cycle {cycle_id} not found on plan {plan_id}.",
            context={"plan_id": plan_id, "cycle_id": cycle_id},
        )
```

In `backend/src/api/exceptions.py`, add to the 404 block:

```python
    "CYCLE_NOT_FOUND": 404,
```

- [ ] **Step 5: Write the router**

Create `backend/src/api/routers/evidence.py`:

```python
"""GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence — what was verified, and
where the code went.

Every fact here already existed. Accepted evidence hung four levels deep at
`active_cycle.goals[].tasks[].verification_evidence[]`, protected scope was
split between the contract and the test bundle with nothing joining it, and the
disposition sat on the cycle — all reachable only by downloading the whole plan
document, which also carries the brief, the chat and every superseded cycle.

Addressed per CYCLE rather than per plan so a superseded cycle's evidence
survives a replan and stays addressable.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.domain.entities.planning_artifacts import Cycle
from src.domain.entities.task import Task
from src.infra.container import AppContainer
from src.infra.errors import CycleNotFoundError

router = APIRouter(prefix="/plans", tags=["evidence"])


class PromotionResponse(BaseModel):
    from_ref: str
    into_ref: str
    merge_sha: str
    promoted_at: datetime


class ProtectedScopeResponse(BaseModel):
    """The two halves an operator previously joined by hand: what the task was
    allowed to touch, and what it was forbidden to weaken."""

    allowed_scope: list[str]
    forbidden_scope: list[str]
    protected_file_hashes: dict[str, str]
    criterion_to_tests: dict[str, list[str]]


class TestBundleResponse(BaseModel):
    test_commit_sha: str
    state: str
    verification_strategy: str


class EvidenceResponse(BaseModel):
    id: str
    run_id: str
    task_revision: int
    verification_kind: str
    exact_command: str
    exit_code: int
    candidate_commit_sha: str
    test_commit_sha: str
    bounded_output_ref: str
    finished_at: datetime


class TaskEvidenceResponse(BaseModel):
    task_id: str
    revision: int
    status: str
    protected_scope: ProtectedScopeResponse | None
    test_bundle: TestBundleResponse | None
    accepted_evidence: list[EvidenceResponse]
    rejected_evidence_count: int
    superseded_evidence_count: int


class GoalEvidenceResponse(BaseModel):
    goal_id: str
    status: str
    promotion: PromotionResponse | None
    tasks: list[TaskEvidenceResponse]


class DispositionResponse(BaseModel):
    disposition: str
    output_reference: str | None


class CycleEvidenceResponse(BaseModel):
    plan_id: str
    cycle_id: str
    cycle_status: str
    goals: list[GoalEvidenceResponse]
    disposition: DispositionResponse | None
    unattributed_evidence_refs: list[str]


def _task_evidence(task: Task) -> TaskEvidenceResponse:
    # Evidence bound to a superseded revision is NOT accepted evidence for the
    # current contract: `edit_task` invalidates revision-bound evidence, so
    # serving it as accepted is precisely the lie this endpoint exists to avoid.
    accepted = [
        item
        for item in task.verification_evidence
        if item.accepted and item.task_revision == task.revision
    ]
    superseded = [
        item
        for item in task.verification_evidence
        if item.accepted and item.task_revision != task.revision
    ]
    rejected = [item for item in task.verification_evidence if not item.accepted]

    contract = task.contract
    bundle = task.test_bundle
    return TaskEvidenceResponse(
        task_id=task.id,
        revision=task.revision,
        status=task.status.value,
        protected_scope=(
            None
            if contract is None
            else ProtectedScopeResponse(
                allowed_scope=list(contract.allowed_scope),
                forbidden_scope=list(contract.forbidden_scope),
                protected_file_hashes=(
                    {} if bundle is None else dict(bundle.protected_file_hashes)
                ),
                criterion_to_tests=(
                    {} if bundle is None else dict(bundle.criterion_to_tests)
                ),
            )
        ),
        test_bundle=(
            None
            if bundle is None
            else TestBundleResponse(
                test_commit_sha=bundle.test_commit_sha,
                state=bundle.state.value,
                verification_strategy=bundle.verification_strategy.value,
            )
        ),
        accepted_evidence=[
            EvidenceResponse(
                id=item.id,
                run_id=item.run_id,
                task_revision=item.task_revision,
                verification_kind=item.verification_kind.value,
                exact_command=item.exact_command,
                exit_code=item.exit_code,
                candidate_commit_sha=item.candidate_commit_sha,
                test_commit_sha=item.test_commit_sha,
                bounded_output_ref=item.bounded_output_ref,
                finished_at=item.finished_at,
            )
            for item in accepted
        ],
        # Counted, not inlined: the full attempt history already has a home at
        # GET .../attempts, and dumping it here would rebuild the very problem
        # this endpoint solves.
        rejected_evidence_count=len(rejected),
        superseded_evidence_count=len(superseded),
    )


@router.get(
    "/{plan_id}/cycles/{cycle_id}/evidence",
    response_model=CycleEvidenceResponse,
)
def get_cycle_evidence(
    plan_id: str,
    cycle_id: str,
    container: AppContainer = Depends(get_container),
) -> CycleEvidenceResponse:
    uow = container.new_unit_of_work()
    with uow:
        plan = uow.plans.get(plan_id)
        promotions = uow.promotions.list_for_cycle(plan_id, cycle_id)

    # Scoped to THIS plan's cycles, so a cycle id belonging to another plan is
    # refused rather than served empty.
    cycle: Cycle | None = next(
        (item for item in plan.cycles if item.id == cycle_id), None
    )
    if cycle is None:
        raise CycleNotFoundError(plan_id, cycle_id)

    by_goal = {item.goal_id: item for item in promotions}
    return CycleEvidenceResponse(
        plan_id=plan_id,
        cycle_id=cycle_id,
        cycle_status=cycle.status.value,
        goals=[
            GoalEvidenceResponse(
                goal_id=goal.id,
                status=goal.status.value,
                promotion=(
                    None
                    if goal.id not in by_goal
                    else PromotionResponse(
                        from_ref=by_goal[goal.id].from_ref,
                        into_ref=by_goal[goal.id].into_ref,
                        merge_sha=by_goal[goal.id].merge_sha,
                        promoted_at=by_goal[goal.id].promoted_at,
                    )
                ),
                tasks=[_task_evidence(task) for task in goal.tasks],
            )
            for goal in cycle.goals
        ],
        disposition=(
            None
            if cycle.output_disposition is None
            else DispositionResponse(
                disposition=cycle.output_disposition.value,
                output_reference=cycle.output_reference,
            )
        ),
        # Cycles promoted before migration 0017 have SHAs with no attribution.
        # Serving them under an honest name beats an empty `promotion` that
        # would imply nothing was ever promoted.
        #
        # Matched by SHA rather than "are there any rows at all": exactly one
        # cycle per install can straddle the migration, with goals promoted
        # before it (ref, no row) and after it (both). A presence check would
        # return [] for that cycle and silently hide the pre-migration refs.
        unattributed_evidence_refs=[
            ref
            for ref in cycle.evidence_refs
            if ref not in {f"git:{item.merge_sha}" for item in promotions}
        ],
    )
```

- [ ] **Step 6: Register the router**

In `backend/src/api/server.py`, add the import next to the other routers and register it **guarded**, after `workers`:

```python
app.include_router(evidence.router, prefix=_prefix, dependencies=_guarded)
```

Evidence carries commands, commit SHAs and output refs. It is control-plane data.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/integration/test_cycle_evidence_api.py tests/integration/test_default_cyclic_execution.py -v && mypy src && ruff check src tests`
Expected: all PASS, mypy zero errors.

- [ ] **Step 8: Commit**

```bash
git add backend/src/api/routers/evidence.py backend/tests/integration/cyclic_walk.py backend/tests/integration/test_cycle_evidence_api.py backend/tests/integration/test_default_cyclic_execution.py backend/src/infra/errors.py backend/src/api/exceptions.py backend/src/api/server.py
git commit -m "feat(api): one evidence read model per cycle (G9)

Accepted evidence, protected scope joined from both halves, promoted refs
and the disposition, addressed per cycle so a superseded cycle's evidence
survives a replan."
```

---

### Task 6: The read model's edge cases

The happy path is not where a read model lies. These six cases are.

**Files:**
- Modify: `backend/tests/integration/test_cycle_evidence_api.py`

**Interfaces:**
- Consumes: the endpoint from Task 5. Produces no new interface.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_cycle_evidence_api.py`:

```python
def test_edited_task_stops_serving_its_stale_evidence_as_accepted(
    evidence_client_with_edit,
) -> None:
    """`edit_task` invalidates revision-bound evidence. Serving it as accepted
    would make the endpoint claim the current contract is satisfied when it is
    not."""
    client, plan_id, cycle_id, task_id = evidence_client_with_edit

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    task = next(
        item
        for goal in body["goals"]
        for item in goal["tasks"]
        if item["task_id"] == task_id
    )

    assert task["accepted_evidence"] == []
    assert task["superseded_evidence_count"] >= 1


def test_cycle_id_from_another_plan_is_refused_not_served_empty(
    evidence_client, other_plan
) -> None:
    client, _, cycle_id = evidence_client
    response = client.get(f"/api/plans/{other_plan}/cycles/{cycle_id}/evidence")
    assert response.status_code == 404


def test_discarded_cycle_serves_its_disposition_with_no_reference(
    discarded_cycle_client,
) -> None:
    client, plan_id, cycle_id = discarded_cycle_client
    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    assert body["disposition"]["disposition"] == "discard"
    assert body["disposition"]["output_reference"] is None


def test_pre_0017_cycle_serves_unattributed_refs(evidence_walk) -> None:
    """A cycle promoted before migration 0017 has SHAs in Cycle.evidence_refs
    and no promotion rows. It must say so rather than look unpromoted."""
    from sqlalchemy import text

    client, plan_id, cycle_id = (
        evidence_walk.client,
        evidence_walk.plan_id,
        evidence_walk.cycle_id,
    )

    # Simulate the pre-migration state: drop this cycle's promotion rows.
    # The container is the one the walk built and injected via set_container;
    # there is no `app.state.container` in this codebase.
    with evidence_walk.container.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM goal_promotions WHERE cycle_id = :cycle_id"),
            {"cycle_id": cycle_id},
        )

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    assert body["unattributed_evidence_refs"], "the git:<sha> entries survive"
    assert all(goal["promotion"] is None for goal in body["goals"])


def test_cycle_straddling_the_migration_reports_only_the_unmatched_refs(
    evidence_walk,
) -> None:
    """Exactly one cycle per install can have goals promoted before migration
    0017 (ref, no row) and after it (both). A presence check would return [] and
    hide the pre-migration half."""
    from sqlalchemy import text

    client, plan_id, cycle_id = (
        evidence_walk.client,
        evidence_walk.plan_id,
        evidence_walk.cycle_id,
    )

    with evidence_walk.container.engine.begin() as connection:
        # Orphan exactly one promotion's ref. Works whether the walk promoted
        # one goal or several — do not assume a goal count here.
        orphaned = connection.execute(
            text(
                "SELECT merge_sha FROM goal_promotions "
                "WHERE cycle_id = :cycle_id ORDER BY promoted_at LIMIT 1"
            ),
            {"cycle_id": cycle_id},
        ).scalar_one()
        connection.execute(
            text(
                "DELETE FROM goal_promotions "
                "WHERE cycle_id = :cycle_id AND merge_sha = :sha"
            ),
            {"cycle_id": cycle_id, "sha": orphaned},
        )
        survivors = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT merge_sha FROM goal_promotions WHERE cycle_id = :cycle_id"
                ),
                {"cycle_id": cycle_id},
            ).all()
        ]

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    unattributed = body["unattributed_evidence_refs"]
    assert f"git:{orphaned}" in unattributed, "the pre-migration half is shown"
    for sha in survivors:
        assert f"git:{sha}" not in unattributed, "an attributed ref is not repeated"


def test_superseded_cycle_still_serves_its_evidence(replanned_client) -> None:
    """Replan is source-preserving: the source cycle stays visible and
    immutable. Its evidence must remain addressable after a new cycle
    activates — which is the whole reason this endpoint is keyed on cycle id
    rather than plan id."""
    client, plan_id, source_cycle_id = replanned_client

    response = client.get(f"/api/plans/{plan_id}/cycles/{source_cycle_id}/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["cycle_id"] == source_cycle_id
    assert body["cycle_status"] == "superseded"
    assert any(
        task["accepted_evidence"]
        for goal in body["goals"]
        for task in goal["tasks"]
    ), "the source cycle's accepted evidence survives the replan"
```

Build the four new fixtures on top of `drive_cycle_to_publication` from Task 5:

- `evidence_walk` — returns the whole `CompletedCycle` (not the 3-tuple), so the test can reach `.container` and `.repo`.
- `evidence_client_with_edit` — drive the walk, then call `POST /api/plans/{plan_id}/edits` against a task that already has accepted evidence. Read that route's request schema in `src/api/routers/plans.py` and use it verbatim.
- `other_plan` — create a second plan via `POST /api/plans` and return its id. It needs no cycle; the point is that its id must not serve another plan's cycle.
- `discarded_cycle_client` — `drive_cycle_to_publication(tmp_path, monkeypatch, disposition="discard", output_reference=None)`.
- `replanned_client` — drive the walk with `publish=False`, capture the cycle id, then call `POST /api/plans/{plan_id}/replan` and drive the new intent and draft gates until the replacement cycle activates. Return the client, plan id, and the **source** cycle id. Read the replan route and gate schemas in `src/api/routers/plans.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_cycle_evidence_api.py -v`
Expected: the six new tests FAIL (fixtures missing), the two from Task 5 still PASS.

- [ ] **Step 3: Make them pass**

The endpoint logic from Task 5 already implements all six behaviours — the revision filter, the plan-scoped cycle lookup, the nullable disposition reference, the SHA-matched `unattributed_evidence_refs` fallback, the straddling-cycle case that fallback exists for, and serving any cycle in `plan.cycles` rather than only the active one. This step is building the fixtures until the assertions hold. If any assertion fails against correct fixtures, fix `evidence.py`, not the test.

- [ ] **Step 4: Run the whole suite**

Run:
```bash
pytest -m "not integration"
pytest -m integration
mypy src
ruff check src tests
```
Expected: all PASS, zero mypy errors.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/test_cycle_evidence_api.py
git commit -m "test(api): pin the evidence read model's four edge cases

Stale-revision evidence is not served as accepted, a cross-plan cycle id
is refused rather than served empty, a discarded cycle serves its
disposition with a null reference, and a pre-0017 cycle says its refs are
unattributed instead of looking unpromoted."
```

---

### Task 7: Auth coverage and generated API types

**Files:**
- Modify: `backend/tests/integration/test_control_plane_auth.py`
- Modify: `frontend/openapi.json`, `frontend/src/types/generated/` (regenerated, not hand-edited)

**Interfaces:**
- Consumes: the endpoint from Task 5. Produces no new interface.

- [ ] **Step 1: Add the route to the auth guard test**

Open `backend/tests/integration/test_control_plane_auth.py`. It parametrizes over the mutating surface from P4.1. Add the evidence route to whatever collection drives the parametrization, following the file's existing entry format exactly:

```python
    ("GET", "/api/plans/{plan_id}/cycles/{cycle_id}/evidence"),
```

- [ ] **Step 2: Run it to verify the route is guarded**

Run: `pytest tests/integration/test_control_plane_auth.py -v`
Expected: PASS. If it FAILS with a 200 where 401 was expected, the router was registered without `dependencies=_guarded` in Task 5 — fix `server.py`.

- [ ] **Step 3: Regenerate the API types**

Run from `frontend/`:
```bash
npm run generate:api
npx tsc --noEmit
npm run build
```
Expected: all clean. `src/types/ui.ts` needs **no** change — it hand-declares the plan DETAIL read model, and this is a new endpoint that does not alter plan detail.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_control_plane_auth.py frontend/openapi.json frontend/src/types/generated/
git commit -m "chore(api): guard the evidence route and regenerate types"
```

---

### Task 8: The fixture reads promoted refs instead of guessing them

**Files:**
- Modify: `fixtures/happy-path-v2/scripts/verify_run.py:388-406`

**Interfaces:**
- Consumes: the endpoint from Task 5.

**Do not touch `fixtures/happy-path-v1/`** — CLAUDE.md marks it locked.

- [ ] **Step 1: Read the current reconstruction**

Read `fixtures/happy-path-v2/scripts/verify_run.py` around lines 385-410. It builds `cycle_branch = f"cycle/{cycle_id}"` (`:390`) and `branch = f"goal/{goal_id}"` (`:404`), then verifies each with `git rev-parse --verify`.

- [ ] **Step 2: Replace the reconstruction with the served refs**

Fetch the evidence document and use its refs, keeping the `git rev-parse --verify` check — the point is to verify the ref the API *claims*, which is a strictly stronger assertion than verifying one the script invented:

```python
    evidence = api_get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence")

    # Verify the ref the API CLAIMS, not one this script reconstructed. A
    # reconstruction can only ever confirm the convention agrees with itself.
    promotions = [
        goal["promotion"] for goal in evidence["goals"] if goal["promotion"]
    ]
    assert promotions, "a completed cycle must report where its goals landed"

    for promotion in promotions:
        for ref in (promotion["from_ref"], promotion["into_ref"]):
            run(["git", "rev-parse", "--verify", ref], cwd=repo)
        run(["git", "cat-file", "-e", promotion["merge_sha"]], cwd=repo)
```

Use the script's own existing HTTP helper and subprocess helper rather than the `api_get` / `run` names above — read the file and match what it already defines, including how it passes the API token.

- [ ] **Step 3: Run the fixture**

Follow `fixtures/happy-path-v2/README.md`. This is **Tier 0** — stub reasoner plus dry-run runner. Never mix tiers.

Expected: the walkthrough completes and `verify_run.py` passes with the served refs.

- [ ] **Step 4: Commit**

```bash
git add fixtures/happy-path-v2/scripts/verify_run.py
git commit -m "test(fixtures): verify promoted refs the API serves, not ones we guess

A reconstruction only confirms the convention agrees with itself, and the
cyclic ladder does not follow the convention that was documented."
```

---

### Task 9: Documentation

A doc contradicting the code is a bug in the doc, fixed in the same PR.

**Files:**
- Modify: `CLAUDE.md:79`
- Modify: `docs/architecture/execution-model.md:168`
- Modify: `docs/architecture/data-model.md`
- Modify: `docs/architecture/capability-matrix.md:188-193` and `:308-319`

**Do not modify `docs/history/`** — it is an immutable archive, and its copies of the old ladder are correct as history.

- [ ] **Step 1: Fix the ladder in CLAUDE.md**

Replace the first sentence of the Git Workspace Rules bullet at `:79`:

```markdown
- Git staging has two shapes. **Cyclic (current)**: project default branch → `cycle/<cycle_id>` → `goal/<goal_id>` → `task/<task_id>/<run_id>` — there is **no** `plan/<plan_id>` rung; the cycle branch is cut straight from the default branch. **Legacy (pre-cyclic rows only)**: default branch → `plan/<plan_id>` → `task/<task_id>/a<attempt>`. Both shapes are defined in one place, `src/app/branch_names.py`, which `project_workspace.py` / `workspace.py` and the execution handler all import. Workers execute each attempt in a worktree on the task branch; **only independently verified work moves upward** a level — a goal branch never reaches the cycle branch until every task is DONE with accepted revision-bound evidence.
```

- [ ] **Step 2: Fix execution-model.md**

Replace `:168` so it names both paths:

```markdown
- `begin` → on a cyclic plan, branch `task/<task_id>/<run_id>` off `goal/<goal_id>` (itself cut off `cycle/<cycle_id>`, which is cut off the repository's detected default branch); on a legacy plan, branch `task/<task_id>/a<attempt-number>` off `plan/<plan_id>`. Plus a temp worktree. The cyclic form keys on the globally unique run id so a retry never reuses a prior attempt's branch; the legacy attempt number is monotonic across the task lifetime, unlike the resettable domain retry counter. `branch -f` makes begin idempotent against a crashed prior invocation. Names come from `src/app/branch_names.py`.
```

- [ ] **Step 3: Add the table to data-model.md**

Add a `goal_promotions` entry following the file's existing format for a plan-scoped table. Content to convey: one row per successful goal→cycle promotion; `from_ref`/`into_ref` stored as the adapter built them rather than reconstructed; `ON DELETE CASCADE` on `plan_id`; written inside the plan finalize transaction; no backfill, so cycles promoted before migration 0017 have none.

- [ ] **Step 4: Close G9 in the capability matrix**

At `:188-193`, change the four G9 rows' exposure column from `api-only`/`hidden` to the new endpoint, citing `test_cycle_evidence_api.py`. Leave the **"Export a run's evidence bundle"** row open and re-scope its note: `export_plan_runs.py` is a whole-database analytics export, not the J7 evidence answer, and the cycle endpoint does not supersede it.

Delete the `### G9` gap section at `:308-319` and remove it from any gap index or count in the document. Verify the counts stated elsewhere in the file are still accurate after removal.

- [ ] **Step 5: Verify the route inventory test still passes**

The matrix's route inventory is test-locked. Run:

```bash
cd backend && pytest -m integration -k "capability_matrix or route_inventory" -v
```
Expected: PASS. If it fails, the new route must be added to the matrix's route inventory table — read the failure message for the exact expected format.

- [ ] **Step 6: Full verification**

```bash
cd backend
ruff check src tests --fix
mypy src
pytest -m "not integration"
pytest -m integration
alembic heads
cd ../frontend && npm run build
```
Expected: all clean; `alembic heads` shows one head at `0017_goal_promotions`.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md docs/architecture/
git commit -m "docs: correct the git ladder and close G9

CLAUDE.md and execution-model.md documented a ladder the cyclic path does
not follow: no plan/<plan_id> rung, and task branches keyed on run id
rather than attempt number. Both now name both shapes and point at
src/app/branch_names.py as the definition.

The G9 rows move to the new evidence endpoint. The evidence-bundle export
row stays open, re-scoped: export_plan_runs.py is a whole-database
analytics export and was never the J7 answer."
```

---

## Verification Summary

Phase 4 closes when all of these hold:

| Claim | Command |
|---|---|
| G9's objective test passes | `pytest tests/integration/test_cycle_evidence_api.py -v` |
| A superseded cycle's evidence survives a replan | `pytest tests/integration/test_cycle_evidence_api.py -v -k superseded` |
| Promotions are transactional on both backends | `pytest tests/integration/test_goal_promotion_repository.py -v` |
| Deleting a plan leaves no promotion rows | `pytest tests/integration/test_delete_plan_leaves_nothing.py -v` |
| The refactor did not change any branch name | `pytest tests/integration/test_git_workspace.py -v` |
| The route is token-guarded | `pytest tests/integration/test_control_plane_auth.py -v` |
| Nothing else regressed | `pytest -m "not integration"` then `pytest -m integration` |
| Types and lint | `mypy src` · `ruff check src tests` |
| One migration head | `alembic heads` |
| Frontend consumes the new schema | `cd frontend && npm run generate:api && npm run build` |
| An operator can verify a real run through the API | `fixtures/happy-path-v2` walkthrough (Tier 0) |
