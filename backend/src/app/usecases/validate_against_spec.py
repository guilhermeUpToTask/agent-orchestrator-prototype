"""
src/app/usecases/validate_against_spec.py — ValidateAgainstSpec use case.

Responsibility:
  Inspect a candidate artifact (task description, dependency list, or
  directory path) against the loaded ProjectSpec and return a structured
  validation result.

This is the primary integration point between the spec domain and the
task/goal execution layer.  The validator does NOT mutate either the spec
or the task — it only reads and reports.

Design note:
  The use case accepts a ProjectSpec rather than loading it internally.
  This means the orchestrator can load the spec once at startup and pass
  the same instance to every validation call, avoiding redundant I/O.

Result shape:
  ValidationResult
    .passed  bool            — True when no violations were found
    .violations list[str]   — human-readable violation messages
    .warnings   list[str]   — advisory messages (non-blocking)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from src.domain.project_spec import ProjectSpec

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """
    Immutable result of a spec validation run.

    passed     — False when at least one hard violation exists.
    violations — blocking issues (task should be rejected).
    warnings   — advisory issues (task may proceed but operator should review).
    """

    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.passed:
            return "ValidationResult: PASSED"
        summary = "; ".join(self.violations)
        return f"ValidationResult: FAILED — {summary}"


class ValidateAgainstSpec:
    """
    Use case: validate a candidate task against the active ProjectSpec.

    Consumers (task manager, planner, reconciler) call execute() before
    dispatching any task to an agent, ensuring that no work is delegated
    that would violate the project's architectural constraints.
    """

    def __init__(self, spec: ProjectSpec) -> None:
        self._spec = spec

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        *,
        task_description: str = "",
        dependencies: list[str] | None = None,
        directories: list[str] | None = None,
    ) -> ValidationResult:
        """
        Validate the provided artifact fragments against the spec.

        Args:
          task_description: Free-text description of the task to be executed.
            Checked for mentions of forbidden patterns.
          dependencies:     List of dependency names or identifiers the task
            would introduce.  Each is checked against is_allowed_dependency().
          directories:      List of directory paths the task would touch.
            Checked against is_forbidden() and structure rules.

        Returns:
          ValidationResult with .passed=True when the task is clean.
        """
        violations: list[str] = []
        warnings: list[str] = []

        # 1. Spec self-consistency check (should always pass if repo guards work)
        structural_issues = self._spec.validate_structure()
        violations.extend(structural_issues)

        # 2. Task description forbidden-pattern scan
        if task_description:
            desc_violations = self._check_description(task_description)
            violations.extend(desc_violations)

        # 3. Dependency allowlist / blocklist check
        for dep in dependencies or []:
            if not self._spec.is_allowed_dependency(dep):
                violations.append(
                    f"Dependency '{dep}' is forbidden by the project spec "
                    f"(matched a forbidden pattern)."
                )

        # 4. Directory structural check
        for path in directories or []:
            if self._spec.is_forbidden(path):
                violations.append(
                    f"Directory path '{path}' matches a forbidden pattern."
                )
            # Advisory: path is not under any declared directory in the spec
            if self._spec.structure.directories and not self._is_under_declared_dir(path):
                warnings.append(
                    f"Directory '{path}' is not declared in project_spec.yaml "
                    "structure rules.  Consider adding it."
                )

        passed = len(violations) == 0

        log.info(
            "validate_against_spec.result",
            project=self._spec.name,
            passed=passed,
            violation_count=len(violations),
            warning_count=len(warnings),
        )

        return ValidationResult(
            passed=passed,
            violations=violations,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Internal checkers
    # ------------------------------------------------------------------

    def _check_description(self, description: str) -> list[str]:
        """Scan task description for mentions of forbidden patterns."""
        violations: list[str] = []
        desc_lower = description.lower()
        for pattern in self._spec.constraints.forbidden:
            if pattern.lower() in desc_lower:
                violations.append(
                    f"Task description references a forbidden pattern: '{pattern}'."
                )
        return violations

    def _is_under_declared_dir(self, path: str) -> bool:
        """
        Return True if *path* matches or is a sub-path of any declared directory.

        Examples (declared: "src/domain", "src/infra"):
          "src/domain"           → True  (exact match)
          "src/domain/entities"  → True  (sub-path)
          "src/new_module"       → False (not declared)
          "src"                  → False (parent of declared, but not itself declared)
        """
        normalised = path.strip("/")
        for rule in self._spec.structure.directories:
            declared = rule.name.strip("/")
            if normalised == declared or normalised.startswith(declared + "/"):
                return True
        return False
