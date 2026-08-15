"""Infrastructure-layer errors.

The domain owns DomainError (business-rule violations); this module owns the
failures of the machinery itself (database locked, adapter I/O). Kept in infra —
neither domain nor app may depend on it.
"""

from __future__ import annotations

from praxis_orchestrator.domain.errors.base import BaseAppException


class InfrastructureError(BaseAppException):
    """An infrastructure operation failed (DB, filesystem, subprocess, network)."""

    code = "INFRASTRUCTURE_ERROR"


class SecretNotFoundError(InfrastructureError):
    """No secret stored under the requested URI."""

    code = "SECRET_NOT_FOUND"


class ProjectBindingInvalidError(InfrastructureError):
    """A project names a repository that cannot be used as one."""

    code = "PROJECT_BINDING_INVALID"


class AttemptNotFoundError(InfrastructureError):
    code = "ATTEMPT_NOT_FOUND"

    def __init__(self, attempt_id: str) -> None:
        super().__init__(f"Attempt {attempt_id} not found.", context={"attempt_id": attempt_id})


class CycleNotFoundError(InfrastructureError):
    """No such cycle on this plan. Follows AttemptNotFoundError: a lookup miss
    for a non-domain identifier still travels as a coded error, never a router
    HTTPException — the status map in praxis_orchestrator/api/exceptions.py is the one table."""

    code = "CYCLE_NOT_FOUND"

    def __init__(self, plan_id: str, cycle_id: str) -> None:
        super().__init__(
            f"Cycle {cycle_id} not found on plan {plan_id}.",
            context={"plan_id": plan_id, "cycle_id": cycle_id},
        )


class UnauthorizedError(BaseAppException):
    """Request lacked valid credentials (control-plane token)."""

    code = "UNAUTHORIZED"
