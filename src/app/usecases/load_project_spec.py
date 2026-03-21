"""
src/app/usecases/load_project_spec.py — LoadProjectSpec use case.

Responsibility:
  Load the ProjectSpec aggregate for the current project from its canonical
  YAML file and return it ready for injection into downstream consumers
  (planner, validator, orchestrator).

This use case is intentionally trivial — its value lies in being the
single, named entry-point so that:
  1. Logging and error translation happen in one place.
  2. The orchestrator and other callers never import the repository directly.
  3. Future caching can be added here without touching callers.

Error handling:
  SpecNotFoundError  — propagated as-is; callers decide whether to abort
                       or prompt the user to run 'orchestrate init'.
  SpecValidationError — propagated as-is; indicates a corrupt or manually
                        edited spec file.
"""
from __future__ import annotations

import structlog

from src.domain.project_spec import ProjectSpec, ProjectSpecRepository

log = structlog.get_logger(__name__)


class LoadProjectSpec:
    """
    Use case: load the ProjectSpec aggregate for a named project.

    Inject via the factory / DI container so the concrete repository
    implementation is never referenced outside the infra layer.
    """

    def __init__(self, spec_repo: ProjectSpecRepository) -> None:
        self._repo = spec_repo

    def execute(self, project_name: str) -> ProjectSpec:
        """
        Load and return the ProjectSpec for *project_name*.

        Raises:
          SpecNotFoundError   — no spec file exists for this project.
          SpecValidationError — spec file is present but invalid.
        """
        log.info("load_project_spec.loading", project=project_name)
        spec = self._repo.load(project_name)
        log.info(
            "load_project_spec.loaded",
            project=project_name,
            version=spec.meta.version,
        )
        return spec
