"""
src/domain/project_spec/errors.py — Domain errors specific to ProjectSpec.
"""
from __future__ import annotations

from src.domain.errors.base import DomainError


class SpecNotFoundError(DomainError):
    """Raised when a project_spec.yaml cannot be located for a given project."""

    def __init__(self, project_name: str) -> None:
        super().__init__(
            f"No project spec found for project '{project_name}'. "
            "Run 'orchestrate init' to create one."
        )
        self.project_name = project_name


class SpecValidationError(DomainError):
    """Raised when the spec YAML fails schema validation."""

    def __init__(self, project_name: str, details: str) -> None:
        super().__init__(
            f"project_spec.yaml for '{project_name}' failed validation: {details}"
        )
        self.project_name = project_name
        self.details = details


class SpecVersionMismatchError(DomainError):
    """
    Raised when the loaded spec version is older than the minimum supported
    version expected by this release of the orchestrator.
    """

    def __init__(self, found: str, minimum: str) -> None:
        super().__init__(
            f"Spec version '{found}' is below the required minimum '{minimum}'. "
            "Please upgrade your project_spec.yaml."
        )
        self.found = found
        self.minimum = minimum


class ForbiddenMutationError(DomainError):
    """
    Raised when something outside the approved flow attempts to mutate the spec.
    Agents and use cases must go through ProposeSpecChange, never write directly.
    """

    def __init__(self, actor: str) -> None:
        super().__init__(
            f"Actor '{actor}' attempted to mutate ProjectSpec directly. "
            "All spec changes must go through ProposeSpecChange and operator approval."
        )
        self.actor = actor
