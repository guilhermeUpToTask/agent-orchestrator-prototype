"""
src/domain/errors/plan_errors.py — ProjectPlan-specific domain errors.

Inherits from ValueError so existing catch-sites continue to work.
"""
from __future__ import annotations

from src.domain.errors.base import DomainError


class InvalidPlanTransitionError(DomainError, ValueError):
    """
    Raised when a ProjectPlan lifecycle action is attempted while the plan
    is not in one of the statuses that allow it.

    Carries the machine-readable context (action, current status, expected
    statuses) so the API layer can build structured 409 responses.

    Example:
        plan.approve_brief(brief)  # plan is 'phase_review' → raises this
    """

    def __init__(self, action: str, current_status: str, expected: list[str]) -> None:
        self.action = action
        self.current_status = current_status
        self.expected = expected
        super().__init__(
            f"Cannot {action}: plan status is '{current_status}'; "
            f"expected one of {expected}."
        )
