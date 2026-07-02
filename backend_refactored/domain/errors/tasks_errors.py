from __future__ import annotations

from domain.errors.base import DomainError


class GoalNotFoundError(DomainError):
    code = "GOAL_NOT_FOUND"

    def __init__(self, goal_id: str) -> None:
        self.goal_id = goal_id
        super().__init__(
            f"Goal '{goal_id}' not found in plan.", context={"goal_id": goal_id}
        )


class TaskNotFoundError(DomainError):
    code = "TASK_NOT_FOUND"

    def __init__(self, task_id: str, goal_id: str) -> None:
        self.task_id = task_id
        self.goal_id = goal_id
        super().__init__(
            f"Task '{task_id}' not found in goal '{goal_id}'.",
            context={"task_id": task_id, "goal_id": goal_id},
        )


class InvalidTransitionError(DomainError):
    """A state transition was attempted that the current status does not allow."""

    code = "INVALID_TRANSITION"

    def __init__(self, entity: str, entity_id: str, frm: str, to: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(
            f"{entity} '{entity_id}' cannot transition from {frm} to {to}.",
            context={"entity": entity, "entity_id": entity_id, "from": frm, "to": to},
        )


class GoalAlreadyRunningError(DomainError):
    """Edit/mutation rejected because the goal is already running or finished."""

    code = "GOAL_ALREADY_RUNNING"

    def __init__(self, goal_id: str, status: str) -> None:
        self.goal_id = goal_id
        super().__init__(
            f"Goal '{goal_id}' is {status}; it can no longer be edited.",
            context={"goal_id": goal_id, "status": status},
        )


class StaleVersionError(DomainError):
    """Optimistic-lock failure: the plan changed since it was read."""

    code = "STALE_VERSION"

    def __init__(self, plan_id: str, expected: int, actual: int) -> None:
        self.plan_id = plan_id
        super().__init__(
            f"Plan '{plan_id}' version conflict (expected {expected}, found {actual}).",
            context={"plan_id": plan_id, "expected": expected, "actual": actual},
        )
