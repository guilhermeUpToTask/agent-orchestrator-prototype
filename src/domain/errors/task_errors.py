"""
src/domain/errors/task_errors.py — Task-specific domain errors.

All errors inherit from built-in types so existing catch-sites that
catch ValueError or plain Exception continue to work without changes.
"""
from __future__ import annotations

from src.domain.errors.base import DomainError


class InvalidStatusTransitionError(DomainError, ValueError):
    """
    Raised when a state-transition method is called on a TaskAggregate
    that is not in one of the allowed source statuses.

    Example:
        task.start()  # task is CREATED, not ASSIGNED → raises this
    """

    def __init__(self, task_id: str, current: str, allowed: list[str]) -> None:
        self.task_id = task_id
        self.current = current
        self.allowed = allowed
        super().__init__(
            f"Task {task_id}: expected status in {allowed}, got '{current}'"
        )


class MaxRetriesExceededError(DomainError, ValueError):
    """
    Raised when requeue() is called on a task whose retry budget is exhausted.

    Example:
        task.requeue()  # attempt == max_retries → raises this
    """

    def __init__(self, task_id: str, attempt: int, max_retries: int) -> None:
        self.task_id = task_id
        self.attempt = attempt
        self.max_retries = max_retries
        super().__init__(
            f"Task {task_id} exceeded max retries ({max_retries})"
        )


class ForbiddenFileEditError(DomainError):
    """
    Raised by ExecutionSpec.validate_modifications() when the agent modified
    files outside the declared allowed set.

    Example:
        spec.validate_modifications(["secret.py"])  # not in allowed list
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(str(violations))
