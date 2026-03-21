"""
src/domain/project_spec/repository.py — ProjectSpec persistence port.

Defines the abstract repository interface that all infrastructure adapters
must satisfy.  Application code must depend only on this interface, never
on a concrete implementation (hexagonal architecture).

Contract:
  load(project_name) → ProjectSpec
    Load and reconstruct the aggregate from its canonical YAML file.
    Raises SpecNotFoundError when no file exists for that project.
    Raises SpecValidationError when the YAML is present but invalid.

  save(spec) → None
    Persist the aggregate to disk.  Only called after an approved change
    has been applied (ProposeSpecChange approval flow).  Must be atomic.

Design note on "no direct agent writes":
  The interface intentionally exposes only load() and save().  There is no
  patch(), update_field(), or similar — the only way to produce a new
  version of the spec is to call ProjectSpec._apply_approved_change() and
  then hand the result to save().  This enforces the approval-gate invariant
  at the type level.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.project_spec.aggregate import ProjectSpec


class ProjectSpecRepository(ABC):
    """
    Port (interface) for ProjectSpec persistence.

    Implementations:
      FileProjectSpecRepository — YAML file under .orchestrator/<project>/
    """

    @abstractmethod
    def load(self, project_name: str) -> ProjectSpec:
        """
        Load the aggregate for *project_name*.

        Raises:
          SpecNotFoundError  — when project_spec.yaml does not exist.
          SpecValidationError — when the file exists but fails schema validation.
        """
        ...

    @abstractmethod
    def save(self, spec: ProjectSpec) -> None:
        """
        Atomically persist *spec* to disk.

        The YAML file is the canonical snapshot — this method must guarantee
        that a crash mid-write leaves the previous version intact (i.e. it
        must use a temp-file + atomic rename pattern).

        Raises:
          OSError — on filesystem errors (permissions, disk full, etc.).
        """
        ...

    def exists(self, project_name: str) -> bool:
        """
        Return True if a spec file exists for *project_name*.

        Default implementation calls load() and catches SpecNotFoundError.
        Concrete implementations may override with a cheaper existence check.
        """
        from src.domain.project_spec.errors import SpecNotFoundError

        try:
            self.load(project_name)
            return True
        except SpecNotFoundError:
            return False
