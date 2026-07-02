from __future__ import annotations

from src.domain.errors.base import DomainError


class EmptyPlanError(DomainError):
    """A plan must have a brief / cannot be created empty."""

    code = "EMPTY_PLAN"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid plan: {reason}.", context={"reason": reason})


class PlanNotFoundError(DomainError):
    code = "PLAN_NOT_FOUND"

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(
            f"Plan '{plan_id}' not found.", context={"plan_id": plan_id}
        )


class InvalidEditError(DomainError):
    code = "INVALID_EDIT"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid edit: {reason}.", context={"reason": reason})


class PlanAlreadyTerminalError(DomainError):
    """Operation rejected because the plan is already DONE or FAILED."""

    code = "PLAN_ALREADY_TERMINAL"

    def __init__(self, plan_id: str, phase: str) -> None:
        self.plan_id = plan_id
        super().__init__(
            f"Plan '{plan_id}' is already {phase}; no further changes allowed.",
            context={"plan_id": plan_id, "phase": phase},
        )
