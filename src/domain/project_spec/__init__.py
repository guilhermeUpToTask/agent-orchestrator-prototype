"""
src/domain/project_spec/ — ProjectSpec domain module public API.

Import everything spec-related from here:

  from src.domain.project_spec import (
      ProjectSpec,
      ProjectSpecRepository,
      SpecVersion,
      SpecNotFoundError,
      SpecValidationError,
  )
"""

from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.project_spec.errors import (
    ForbiddenMutationError,
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionMismatchError,
)
from src.domain.project_spec.repository import ProjectSpecRepository
from src.domain.project_spec.value_objects import (
    DirectoryRule,
    SpecConstraints,
    SpecObjective,
    SpecVersion,
    StructureSpec,
    TechStack,
)

__all__ = [
    # Aggregate
    "ProjectSpec",
    # Repository port
    "ProjectSpecRepository",
    # Value objects
    "SpecVersion",
    "TechStack",
    "SpecConstraints",
    "StructureSpec",
    "DirectoryRule",
    "SpecObjective",
    # Errors
    "SpecNotFoundError",
    "SpecValidationError",
    "SpecVersionMismatchError",
    "ForbiddenMutationError",
]
