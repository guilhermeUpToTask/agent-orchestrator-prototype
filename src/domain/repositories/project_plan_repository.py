"""
src/domain/repositories/project_plan_repository.py — ProjectPlan repository port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.aggregates.project_plan import ProjectPlan


class ProjectPlanRepositoryPort(ABC):
    """
    Contract for persisting and retrieving the single project plan.

    One plan per project — no plan_id parameter on load. The repository
    knows the project scope from its constructor.
    """

    @abstractmethod
    def save(self, plan: ProjectPlan) -> None:
        """Persist the plan to storage."""
        ...

    @abstractmethod
    def load(self) -> ProjectPlan:
        """Load the plan from storage.

        Raises KeyError if no plan exists.
        """
        ...

    @abstractmethod
    def exists(self) -> bool:
        """Return True if a plan exists in storage."""
        ...

    @abstractmethod
    def get(self) -> Optional[ProjectPlan]:
        """Return the plan if it exists, None otherwise."""
        ...
