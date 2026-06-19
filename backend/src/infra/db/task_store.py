"""
src/infra/db/task_store.py — SQLite implementation of TaskRepositoryPort.

Stage B of the file->SQLite migration. The full TaskAggregate is stored as JSON
in ``tasks.data``; ``status``/``state_version``/``project_id`` are projections
for querying and CAS. Every successful transition also appends an immutable row
to ``task_transitions``.

Persist-first is preserved by *commit-then-emit*: this store commits the
transaction (durable under synchronous=FULL) and returns; the use case then
publishes to Redis. A crash between commit and XADD is recoverable — the
committed state is re-derived by the reconciler, exactly as with the YAML
adapter's fsync-then-publish.

Leases remain in Redis (LeasePort); this store is task *state* only.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar, cast

import structlog
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from src.app.errors import InfrastructureException
from src.domain.aggregates.task import TaskAggregate
from src.domain.repositories.task_repository import TaskRepositoryPort
from src.domain.value_objects.task import HistoryEntry
from src.infra.db.tables import TaskTable, TaskTransitionTable

log = structlog.get_logger(__name__)

_T = TypeVar("_T")
_MAX_LOCK_RETRIES = 5
_LOCK_BACKOFF_BASE = 0.05


def _is_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


class SqliteTaskStore(TaskRepositoryPort):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def _run(self, fn: Callable[[Session], _T]) -> _T:
        last: OperationalError | None = None
        for attempt in range(_MAX_LOCK_RETRIES):
            try:
                with self._sf() as session:
                    result = fn(session)
                    session.commit()
                    return result
            except OperationalError as exc:
                if not _is_locked(exc):
                    raise
                last = exc
                time.sleep(_LOCK_BACKOFF_BASE * (2 ** attempt))
                log.warning("task_store.locked_retry", attempt=attempt)
        raise InfrastructureException(
            "Task store stayed locked beyond retry budget", code="DB_LOCKED"
        ) from last

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    def load(self, task_id: str) -> TaskAggregate:
        with self._sf() as s:
            row = s.get(TaskTable, task_id)
            if row is None:
                raise KeyError(f"Task {task_id} not found")
            return TaskAggregate.model_validate(row.data)

    def update_if_version(
        self,
        task_id: str,
        new_state: TaskAggregate,
        expected_version: int,
    ) -> bool:
        def _op(s: Session) -> bool:
            row = s.get(TaskTable, task_id)
            if row is None:
                raise KeyError(f"Task {task_id} not found")
            if row.state_version != expected_version:
                return False  # version conflict
            result = cast(CursorResult, s.execute(
                update(TaskTable)
                .where(TaskTable.task_id == task_id, TaskTable.state_version == expected_version)
                .values(
                    status=new_state.status.value,
                    state_version=new_state.state_version,
                    project_id=new_state.project_id,
                    data=new_state.model_dump(mode="json"),
                )
            ))
            if result.rowcount == 0:
                return False
            _append_transition(s, new_state)
            return True
        return self._run(_op)

    def save(self, task: TaskAggregate) -> None:
        def _op(s: Session) -> None:
            row = s.get(TaskTable, task.task_id)
            payload = task.model_dump(mode="json")
            if row is None:
                s.add(TaskTable(
                    task_id=task.task_id,
                    project_id=task.project_id,
                    status=task.status.value,
                    state_version=task.state_version,
                    data=payload,
                ))
            else:
                row.project_id = task.project_id
                row.status = task.status.value
                row.state_version = task.state_version
                row.data = payload
            _append_transition(s, task)
        self._run(_op)

    def append_history(self, task_id: str, event: str, actor: str, detail: dict) -> None:
        def _op(s: Session) -> None:
            row = s.get(TaskTable, task_id)
            if row is None:
                raise KeyError(f"Task {task_id} not found")
            task = TaskAggregate.model_validate(row.data)
            task.history.append(HistoryEntry(event=event, actor=actor, detail=detail))
            row.data = task.model_dump(mode="json")
            s.add(TaskTransitionTable(
                task_id=task_id, event=event, actor=actor,
                detail=detail, state_version=task.state_version,
            ))
        self._run(_op)

    def delete(self, task_id: str) -> bool:
        def _op(s: Session) -> bool:
            result = cast(CursorResult, s.execute(
                delete(TaskTable).where(TaskTable.task_id == task_id)
            ))
            return result.rowcount > 0
        return self._run(_op)

    def list_all(self) -> list[TaskAggregate]:
        with self._sf() as s:
            rows = s.execute(select(TaskTable)).scalars().all()
        tasks: list[TaskAggregate] = []
        for row in rows:
            try:
                tasks.append(TaskAggregate.model_validate(row.data))
            except Exception as exc:  # surface corruption, don't drop the sweep
                log.error("task_store.corrupt_row", task_id=row.task_id, error=str(exc))
        return tasks


def _append_transition(session: Session, task: TaskAggregate) -> None:
    """Append an audit row for the task's most recent history entry, if any."""
    if not task.history:
        return
    last = task.history[-1]
    session.add(TaskTransitionTable(
        task_id=task.task_id,
        event=last.event,
        actor=last.actor,
        detail=last.detail,
        state_version=task.state_version,
    ))
