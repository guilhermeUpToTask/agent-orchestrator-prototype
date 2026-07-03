"""Domain errors — convenience re-exports of the shared bases.

Specific error types are imported from their topic module (tasks_errors,
agent_errors, config_errors, planning_errors); only the bases every layer
needs are re-exported here.
"""

from src.domain.errors.base import BaseAppException, DomainError

__all__ = ["BaseAppException", "DomainError"]
