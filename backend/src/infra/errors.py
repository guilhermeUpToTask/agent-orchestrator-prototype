"""Infrastructure-layer errors.

The domain owns DomainError (business-rule violations); this module owns the
failures of the machinery itself (database locked, adapter I/O). Kept in infra —
neither domain nor app may depend on it.
"""

from __future__ import annotations

from src.domain.errors.base import BaseAppException


class InfrastructureError(BaseAppException):
    """An infrastructure operation failed (DB, filesystem, subprocess, network)."""

    code = "INFRASTRUCTURE_ERROR"


class SecretNotFoundError(InfrastructureError):
    """No secret stored under the requested URI."""

    code = "SECRET_NOT_FOUND"


class AttemptNotFoundError(InfrastructureError):
    code = "ATTEMPT_NOT_FOUND"

    def __init__(self, attempt_id: str) -> None:
        super().__init__(f"Attempt {attempt_id} not found.", context={"attempt_id": attempt_id})


class UnauthorizedError(BaseAppException):
    """Request lacked valid credentials (control-plane token)."""

    code = "UNAUTHORIZED"
