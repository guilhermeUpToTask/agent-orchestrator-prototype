"""
src/app/errors.py — Application-level exceptions.

These sit above the domain rule violations: they describe failures the
application layer detects while orchestrating use cases (bad input, missing
resource, auth, upstream/infra failure). They subclass the same
``BaseAppException`` root as the domain errors, so the whole system speaks one
exception language and the API layer maps every one to an HTTP status in a
single place.

No HTTP status codes here — the code/message pair is transport-agnostic.
"""
from __future__ import annotations

from src.domain.errors.base import BaseAppException


class ValidationException(BaseAppException):
    """Input/shape is invalid before any persistence is attempted."""

    code = "VALIDATION_ERROR"


class ResourceNotFoundException(BaseAppException):
    """A requested entity does not exist."""

    code = "NOT_FOUND"


class UnauthorizedException(BaseAppException):
    """Authentication is missing or invalid."""

    code = "UNAUTHORIZED"


class ForbiddenException(BaseAppException):
    """Authenticated, but not permitted to perform the action."""

    code = "FORBIDDEN"


class ExternalServiceException(BaseAppException):
    """An upstream dependency (GitHub, agent CLI, redis) failed."""

    code = "EXTERNAL_SERVICE_ERROR"


class InfrastructureException(BaseAppException):
    """
    A local infrastructure operation failed beyond recovery — e.g. the DB
    stayed locked past the retry budget, a filesystem write failed, or
    encryption/decryption errored.
    """

    code = "INFRASTRUCTURE_ERROR"


__all__ = [
    "ExternalServiceException",
    "ForbiddenException",
    "InfrastructureException",
    "ResourceNotFoundException",
    "UnauthorizedException",
    "ValidationException",
]
