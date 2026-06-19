"""
src/domain/value_objects/architecture_roadmap.py — the typed architecture roadmap.

The output contract of the ARCHITECTURE planning phase. It wraps the three slices
the planner proposes — the ordered cyclical ``Phase`` sequence, the architectural
``DecisionEntry`` records, and one ``GoalSpec`` per goal referenced by a phase —
into a single immutable, self-validating artifact.

Named ``ArchitectureRoadmap`` to avoid colliding with the goal-DAG ``Roadmap`` in
``value_objects/goal.py`` (that one validates inter-goal dependencies; this one
validates the phase/goal/decision structure of a freshly planned architecture).

Construction fails (``RoadmapValidationError``) when the structure is incoherent,
giving the planner agent precise, actionable feedback to converge on a valid plan.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain.aggregates.project_plan import Phase
from src.domain.ports.project_state import DecisionEntry
from src.domain.value_objects.goal import GoalSpec


class RoadmapValidationError(ValueError):
    """Raised when an ArchitectureRoadmap is structurally invalid.

    Carries the full list of problems so callers (notably the
    ``submit_architecture`` tool) can hand the agent every fix at once.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class ArchitectureRoadmap:
    phases: list[Phase]
    decisions: list[DecisionEntry]
    goal_specs: dict[str, GoalSpec]  # goal_name -> spec

    def __post_init__(self) -> None:
        errors = self.validation_errors(self.phases, self.decisions, self.goal_specs)
        if errors:
            raise RoadmapValidationError(errors)

    @staticmethod
    def validation_errors(
        phases: list[Phase],
        decisions: list[DecisionEntry],
        goal_specs: dict[str, GoalSpec],
    ) -> list[str]:
        """Return all structural problems with these components, or [] if valid.

        Pure and side-effect-free so it can be reused by the assembler/feedback
        loop without constructing (and catching the failure of) the artifact.
        """
        errors: list[str] = []

        if not phases:
            errors.append("Roadmap must contain at least one phase.")
            return errors

        indices = sorted(p.index for p in phases)
        if len(set(indices)) != len(indices):
            errors.append(f"Phase indices must be unique; got {indices}.")
        elif indices != list(range(indices[0], indices[0] + len(indices))):
            errors.append(f"Phase indices must be contiguous; got {indices}.")

        seen: set[str] = set()
        duplicates: set[str] = set()
        for phase in phases:
            if not phase.goal_names:
                errors.append(f"Phase {phase.index} ('{phase.name}') dispatches no goals.")
            for goal_name in phase.goal_names:
                if goal_name in seen:
                    duplicates.add(goal_name)
                seen.add(goal_name)
                if goal_name not in goal_specs:
                    errors.append(
                        f"Goal '{goal_name}' referenced by phase {phase.index} "
                        "has no description; describe it before submitting."
                    )
        if duplicates:
            errors.append(
                f"Duplicate goal names across phases: {sorted(duplicates)}."
            )

        return errors
