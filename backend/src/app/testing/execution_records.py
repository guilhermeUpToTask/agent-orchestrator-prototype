"""In-memory transactional fake for execution run/attempt records."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
)


class InMemoryExecutionRecordRepository:
    """Copy-on-enter transaction semantics matching the bound SQLite adapter."""

    def __init__(self) -> None:
        self._runs: dict[str, ExecutionRun] = {}
        self._attempts: dict[str, ExecutionAttempt] = {}
        self._tx_runs: dict[str, ExecutionRun] | None = None
        self._tx_attempts: dict[str, ExecutionAttempt] | None = None

    def _begin(self) -> None:
        if self._tx_runs is not None:
            raise RuntimeError("execution record transactions cannot be nested")
        self._tx_runs = dict(self._runs)
        self._tx_attempts = dict(self._attempts)

    def _commit(self) -> None:
        assert self._tx_runs is not None and self._tx_attempts is not None
        self._runs = self._tx_runs
        self._attempts = self._tx_attempts
        self._tx_runs = None
        self._tx_attempts = None

    def _rollback(self) -> None:
        self._tx_runs = None
        self._tx_attempts = None

    def _bound(
        self,
    ) -> tuple[dict[str, ExecutionRun], dict[str, ExecutionAttempt]]:
        if self._tx_runs is None or self._tx_attempts is None:
            raise RuntimeError(
                "InMemoryExecutionRecordRepository used outside a UnitOfWork transaction"
            )
        return self._tx_runs, self._tx_attempts

    def find_active_run(self, plan_id: str, goal_id: str, task_id: str) -> ExecutionRun | None:
        runs, _ = self._bound()
        active = [
            run
            for run in runs.values()
            if run.plan_id == plan_id
            and run.goal_id == goal_id
            and run.task_id == task_id
            and run.status in (ExecutionRunStatus.RUNNING, ExecutionRunStatus.RETRYING)
        ]
        if len(active) > 1:
            raise RuntimeError("multiple active execution runs for one task")
        return active[0] if active else None

    def add_run(self, run: ExecutionRun) -> None:
        runs, _ = self._bound()
        if run.id in runs:
            raise RuntimeError(f"duplicate execution run {run.id!r}")
        if self.find_active_run(run.plan_id, run.goal_id, run.task_id) is not None:
            raise RuntimeError("an active execution run already exists for this task")
        runs[run.id] = run

    def next_attempt_number(self, plan_id: str, goal_id: str, task_id: str) -> int:
        _, attempts = self._bound()
        numbers = [
            attempt.number
            for attempt in attempts.values()
            if attempt.plan_id == plan_id
            and attempt.goal_id == goal_id
            and attempt.task_id == task_id
        ]
        return max(numbers, default=0) + 1

    def add_attempt(self, attempt: ExecutionAttempt) -> None:
        runs, attempts = self._bound()
        if attempt.id in attempts:
            raise RuntimeError(f"duplicate execution attempt {attempt.id!r}")
        if attempt.run_id not in runs:
            raise KeyError(attempt.run_id)
        if any(
            existing.plan_id == attempt.plan_id
            and existing.goal_id == attempt.goal_id
            and existing.task_id == attempt.task_id
            and existing.number == attempt.number
            for existing in attempts.values()
        ):
            raise RuntimeError("duplicate task-lifetime execution attempt number")
        attempts[attempt.id] = attempt

    def mark_run_running(self, run_id: str) -> None:
        runs, _ = self._bound()
        run = runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in (ExecutionRunStatus.RUNNING, ExecutionRunStatus.RETRYING):
            raise RuntimeError(f"execution run {run_id!r} is not active")
        runs[run_id] = replace(run, status=ExecutionRunStatus.RUNNING, completed_at=None)

    def finalize_attempt(
        self,
        attempt_id: str,
        *,
        attempt_status: ExecutionAttemptStatus,
        run_status: ExecutionRunStatus,
        completed_at: datetime,
    ) -> None:
        runs, attempts = self._bound()
        attempt = attempts.get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        if attempt.status == ExecutionAttemptStatus.RUNNING:
            attempts[attempt_id] = replace(
                attempt, status=attempt_status, completed_at=completed_at
            )
        elif attempt.status != attempt_status:
            raise RuntimeError(
                f"execution attempt {attempt_id!r} is already {attempt.status.value!r}"
            )
        run = runs[attempt.run_id]
        runs[run.id] = replace(
            run,
            status=run_status,
            completed_at=(None if run_status == ExecutionRunStatus.RETRYING else completed_at),
        )

    def get_run(self, run_id: str) -> ExecutionRun:
        runs = self._runs if self._tx_runs is None else self._tx_runs
        if run_id not in runs:
            raise KeyError(run_id)
        return runs[run_id]

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt:
        attempts = self._attempts if self._tx_attempts is None else self._tx_attempts
        if attempt_id not in attempts:
            raise KeyError(attempt_id)
        return attempts[attempt_id]

    def list_open_attempts(self, plan_id: str | None = None) -> list[ExecutionAttempt]:
        attempts = self._attempts if self._tx_attempts is None else self._tx_attempts
        return sorted(
            (
                attempt
                for attempt in attempts.values()
                if attempt.status == ExecutionAttemptStatus.RUNNING
                and (plan_id is None or attempt.plan_id == plan_id)
            ),
            key=lambda attempt: (attempt.started_at, attempt.id),
        )
