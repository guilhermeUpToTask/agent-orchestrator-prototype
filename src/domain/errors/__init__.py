"""src/domain/errors/ — Domain exceptions (re-exports)."""

from src.domain.errors.base             import BaseAppException, DomainError, DomainException
from src.domain.errors.config_errors    import ConflictException, ReferentialException
from src.domain.errors.capability_errors import UnknownCapabilityError
from src.domain.errors.plan_errors       import InvalidPlanTransitionError
from src.domain.errors.task_errors import (
    ForbiddenFileEditError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
)

__all__ = [
    "BaseAppException",
    "ConflictException",
    "DomainError",
    "DomainException",
    "ForbiddenFileEditError",
    "InvalidPlanTransitionError",
    "InvalidStatusTransitionError",
    "MaxRetriesExceededError",
    "ReferentialException",
    "UnknownCapabilityError",
]
