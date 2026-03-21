"""
src/domain/project_spec/aggregate.py — ProjectSpec Aggregate Root.

The ProjectSpec is the canonical, read-only constraint boundary that all
application use-cases and agents must respect.  It is NOT a configuration
file; it is a domain aggregate that:

  - Encodes the stable constraints of the project (tech stack, architecture,
    forbidden patterns, structural rules).
  - Enforces its own invariants on construction and after any controlled update.
  - Exposes read-only access to every consumer (planner, validator, agents).
  - Prevents uncontrolled mutation — no public setters, only approved methods.

Persistence:
  .orchestrator/<project_name>/project_spec.yaml

The YAML file is the canonical snapshot; this class is the in-memory model.

Invariants (strictly enforced):
  1. No goals, tasks, or execution logic are ever stored here.
  2. Version must be a valid semver string.
  3. tech_stack, constraints, structure are immutable value objects.
  4. Direct field mutation is impossible from outside the aggregate.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.domain.project_spec.value_objects import (
    DirectoryRule,
    SpecConstraints,
    SpecObjective,
    SpecVersion,
    StructureSpec,
    TechStack,
)


class _SpecMeta(BaseModel):
    """Metadata block stored under ``meta:`` in the YAML."""

    name: str
    version: str

    model_config = {"frozen": True}


class ProjectSpec(BaseModel):
    """
    Aggregate Root that represents the stable specification of a project.

    Construction:
      Use ProjectSpec.create() to build from raw data, or let
      FileProjectSpecRepository deserialise from YAML.

    Read access:
      Call the query methods below.  Direct field access to the pydantic
      fields is intentionally allowed for infrastructure serialisation but
      must never be used to mutate state.

    Mutation:
      Only _apply_approved_change() may produce a new version.
      Application code must go through ProposeSpecChange → operator approval.
    """

    # ------------------------------------------------------------------ #
    # Fields — frozen so that Python-level assignments raise an error     #
    # ------------------------------------------------------------------ #

    meta: _SpecMeta
    objective: SpecObjective
    tech_stack: TechStack
    constraints: SpecConstraints
    structure: StructureSpec

    model_config = {"frozen": True}

    # ------------------------------------------------------------------ #
    # Post-construction invariant enforcement                             #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def _enforce_invariants(self) -> "ProjectSpec":
        # Invariant: name must be non-empty
        if not self.meta.name.strip():
            raise ValueError("ProjectSpec.meta.name must not be empty.")

        # Invariant: version must be valid semver (delegated to SpecVersion)
        SpecVersion.from_string(self.meta.version)

        # Invariant: objective description must be non-empty
        if not self.objective.description.strip():
            raise ValueError("ProjectSpec.objective.description must not be empty.")

        return self

    # ------------------------------------------------------------------ #
    # Factory                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        name: str,
        objective_description: str,
        objective_domain: str,
        backend: list[str] | None = None,
        database: list[str] | None = None,
        infra: list[str] | None = None,
        forbidden: list[str] | None = None,
        required: list[str] | None = None,
        directories: list[dict[str, str]] | None = None,
        version: str = "0.1.0",
    ) -> "ProjectSpec":
        """
        Convenience factory for programmatic construction.

        All arguments are plain Python types so callers never need to
        import value objects directly.
        """
        return cls(
            meta=_SpecMeta(name=name, version=version),
            objective=SpecObjective(
                description=objective_description,
                domain=objective_domain,
            ),
            tech_stack=TechStack(
                backend=backend or [],
                database=database or [],
                infra=infra or [],
            ),
            constraints=SpecConstraints(
                forbidden=forbidden or [],
                required=required or [],
            ),
            structure=StructureSpec(
                directories=[
                    DirectoryRule(**d) for d in (directories or [])
                ]
            ),
        )

    # ------------------------------------------------------------------ #
    # Read-only accessors                                                 #
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        """The project name as declared in the spec."""
        return self.meta.name

    @property
    def version(self) -> SpecVersion:
        """The current spec version as a SpecVersion value object."""
        return SpecVersion.from_string(self.meta.version)

    def get_architecture_constraints(self) -> dict[str, Any]:
        """
        Return a read-only summary of all architectural constraints.

        This is the primary integration point for the Planner and Validator.
        The returned dict is safe to pass to agents as context — it contains
        no mutable references.
        """
        return {
            "project": self.meta.name,
            "version": self.meta.version,
            "domain": self.objective.domain,
            "tech_stack": {
                "backend": list(self.tech_stack.backend),
                "database": list(self.tech_stack.database),
                "infra": list(self.tech_stack.infra),
            },
            "constraints": {
                "forbidden": list(self.constraints.forbidden),
                "required": list(self.constraints.required),
            },
            "structure": [
                {"name": d.name, "purpose": d.purpose}
                for d in self.structure.directories
            ],
        }

    def is_allowed_dependency(self, dep: str) -> bool:
        """
        Return True if *dep* is not listed as a forbidden pattern.

        A dependency is forbidden when any forbidden pattern is a
        case-insensitive substring of the dependency string.

        Examples:
          spec.is_allowed_dependency("django")     → False  (if "django" is forbidden)
          spec.is_allowed_dependency("fastapi")    → True   (if "fastapi" is required)
        """
        dep_lower = dep.lower()
        for pattern in self.constraints.forbidden:
            if pattern.lower() in dep_lower:
                return False
        return True

    def is_forbidden(self, pattern: str) -> bool:
        """
        Return True if *pattern* matches any entry in constraints.forbidden.

        Matching is case-insensitive substring containment so callers can
        pass either an exact name or a path fragment.
        """
        pattern_lower = pattern.lower()
        return any(f.lower() in pattern_lower for f in self.constraints.forbidden)

    def is_required(self, pattern: str) -> bool:
        """Return True if *pattern* appears in constraints.required."""
        pattern_lower = pattern.lower()
        return any(r.lower() in pattern_lower for r in self.constraints.required)

    def has_directory(self, name: str) -> bool:
        """Return True if a directory rule with the given name exists."""
        return any(d.name == name for d in self.structure.directories)

    def directory_purpose(self, name: str) -> str | None:
        """Return the declared purpose of *name*, or None if not defined."""
        for d in self.structure.directories:
            if d.name == name:
                return d.purpose
        return None

    def validate_structure(self) -> list[str]:
        """
        Return a list of structural violation messages.

        An empty list means the spec is self-consistent.  This is exposed so
        the Validator can call it without coupling to internals.
        """
        violations: list[str] = []

        # Ensure no directory appears twice
        seen: set[str] = set()
        for rule in self.structure.directories:
            if rule.name in seen:
                violations.append(
                    f"Duplicate directory rule: '{rule.name}' appears more than once."
                )
            seen.add(rule.name)

        # Ensure forbidden and required sets do not overlap
        forbidden_set = set(self.constraints.forbidden)
        required_set = set(self.constraints.required)
        overlap = forbidden_set & required_set
        if overlap:
            violations.append(
                f"Patterns appear in both forbidden and required: {sorted(overlap)}"
            )

        return violations

    # ------------------------------------------------------------------ #
    # Controlled mutation (approved change flow only)                     #
    # ------------------------------------------------------------------ #

    def _apply_approved_change(
        self,
        *,
        new_version: str | None = None,
        new_objective_description: str | None = None,
        new_objective_domain: str | None = None,
        add_forbidden: list[str] | None = None,
        remove_forbidden: list[str] | None = None,
        add_required: list[str] | None = None,
        remove_required: list[str] | None = None,
        add_directory: dict[str, str] | None = None,
        remove_directory: str | None = None,
    ) -> "ProjectSpec":
        """
        Produce a **new** ProjectSpec with the requested changes applied.

        This method is intentionally named with a leading underscore to signal
        that it is NOT part of the public API.  Only the approved change flow
        (ProposeSpecChange → operator approval → FileProjectSpecRepository.save)
        may call this method.

        Returns a new frozen aggregate; never mutates self.
        """
        # --- meta ---
        version_str = new_version if new_version is not None else self.meta.version
        SpecVersion.from_string(version_str)  # validate eagerly

        meta = _SpecMeta(name=self.meta.name, version=version_str)

        # --- objective ---
        objective = SpecObjective(
            description=(
                new_objective_description
                if new_objective_description is not None
                else self.objective.description
            ),
            domain=(
                new_objective_domain
                if new_objective_domain is not None
                else self.objective.domain
            ),
        )

        # --- constraints ---
        current_forbidden = set(self.constraints.forbidden)
        current_required = set(self.constraints.required)

        for p in add_forbidden or []:
            current_forbidden.add(p)
        for p in remove_forbidden or []:
            current_forbidden.discard(p)
        for p in add_required or []:
            current_required.add(p)
        for p in remove_required or []:
            current_required.discard(p)

        constraints = SpecConstraints(
            forbidden=sorted(current_forbidden),
            required=sorted(current_required),
        )

        # --- structure ---
        dirs = list(self.structure.directories)
        if remove_directory is not None:
            dirs = [d for d in dirs if d.name != remove_directory]
        if add_directory is not None:
            dirs.append(DirectoryRule(**add_directory))

        structure = StructureSpec(directories=dirs)

        return ProjectSpec(
            meta=meta,
            objective=objective,
            tech_stack=self.tech_stack,
            constraints=constraints,
            structure=structure,
        )

    # ------------------------------------------------------------------ #
    # Serialisation helpers (used by FileProjectSpecRepository)           #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise to the canonical YAML schema dict.

        Keys match the spec schema exactly so that round-trip
        load → serialise → load is lossless.
        """
        return {
            "meta": {
                "name": self.meta.name,
                "version": self.meta.version,
            },
            "objective": {
                "description": self.objective.description,
                "domain": self.objective.domain,
            },
            "tech_stack": {
                "backend": list(self.tech_stack.backend),
                "database": list(self.tech_stack.database),
                "infra": list(self.tech_stack.infra),
            },
            "constraints": {
                "forbidden": list(self.constraints.forbidden),
                "required": list(self.constraints.required),
            },
            "structure": {
                "directories": [
                    {"name": d.name, "purpose": d.purpose}
                    for d in self.structure.directories
                ]
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectSpec":
        """
        Reconstruct a ProjectSpec from a raw deserialized YAML dict.

        Raises SpecValidationError-friendly ValueError on schema violations.
        """
        try:
            meta_raw = data["meta"]
            objective_raw = data["objective"]
            tech_raw = data.get("tech_stack", {})
            constraints_raw = data.get("constraints", {})
            structure_raw = data.get("structure", {})

            return cls(
                meta=_SpecMeta(
                    name=meta_raw["name"],
                    version=meta_raw["version"],
                ),
                objective=SpecObjective(
                    description=objective_raw["description"],
                    domain=objective_raw["domain"],
                ),
                tech_stack=TechStack(
                    backend=tech_raw.get("backend", []),
                    database=tech_raw.get("database", []),
                    infra=tech_raw.get("infra", []),
                ),
                constraints=SpecConstraints(
                    forbidden=constraints_raw.get("forbidden", []),
                    required=constraints_raw.get("required", []),
                ),
                structure=StructureSpec(
                    directories=structure_raw.get("directories", []),
                ),
            )
        except KeyError as exc:
            raise ValueError(
                f"Missing required field in project_spec.yaml: {exc}"
            ) from exc
