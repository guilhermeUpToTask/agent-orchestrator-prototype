"""src/domain/errors/ — Domain exceptions (re-exports)."""

from src.domain.errors.base        import DomainError
from src.domain.errors.task_errors import (
    ForbiddenFileEditError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
)

__all__ = [
    "DomainError",
    "ForbiddenFileEditError",
    "InvalidStatusTransitionError",
    "MaxRetriesExceededError",
]
