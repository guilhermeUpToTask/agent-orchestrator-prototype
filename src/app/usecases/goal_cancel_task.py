"""
src/app/usecases/goal_cancel_task.py — Handle a permanently canceled task.

Called by the TaskGraphOrchestrator when it receives a task.canceled event
(emitted by TaskFailHandlingUseCase after retries are exhausted).

The use case records the cancellation on the GoalAggregate, which transitions
the goal to FAILED and emits a goal.failed event so operators are notified.
"""
from __future__ import annotations

import structlog

from src.app.telemetry.service import TelemetryService
from src.domain import DomainEvent, EventPort
from src.domain import TelemetryEmitterPort
from src.domain.repositories import TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"
MAX_CAS_RETRIES = 5


class GoalCancelTaskUseCase:
    """
    React to a permanently canceled task and fail the owning goal.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        telemetry_emitter: TelemetryEmitterPort | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._goal_repo = goal_repo
        self._events    = event_port
        self._telemetry = TelemetryService(telemetry_emitter, producer=PRODUCER)

    def execute(self, task_id: str, reason: str) -> None:
        try:
            task = self._task_repo.load(task_id)
        except KeyError:
            log.warning("goal_cancel.task_not_found", task_id=task_id)
            return

        goal_id = task.feature_id
        if not goal_id:
            return
        trace = self._telemetry.start_trace(goal_id=goal_id, correlation_id=goal_id)

        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.get(goal_id)
            if goal is None or goal.is_terminal():
                return
            expected_v = goal.state_version
            goal.record_task_canceled(task_id, reason)
            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                self._telemetry.emit(
                    "goal.failed",
                    self._telemetry.start_span(trace),
                    payload={"goal_id": goal_id, "task_id": task_id, "reason": reason},
                    goal_id=goal_id,
                    task_id=task_id,
                )
                self._events.publish(DomainEvent(
                    type="goal.failed",
                    producer=PRODUCER,
                    payload={
                        "goal_id": goal_id,
                        "task_id": task_id,
                        "reason":  goal.failure_reason or reason,
                    },
                ))
                log.info(
                    "goal_cancel.goal_failed",
                    goal_id=goal_id,
                    task_id=task_id,
                    reason=reason,
                )
                return
            log.warning(
                "goal_cancel.cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )
        log.error("goal_cancel.cas_exhausted", goal_id=goal_id, task_id=task_id)
