"""
src/app/planning/roadmap_assembler.py — build a typed ArchitectureRoadmap.

Centralizes what used to be scattered across ``PlanningSessionSupport`` —
``extract_pending_decisions`` / ``extract_pending_phases`` / ``find_goal_spec`` —
into one place that produces a validated :class:`ArchitectureRoadmap` (or a list
of actionable errors) from the raw ``session.roadmap_data`` dict.

Storage stays an untyped dict (the repository is unchanged); typing happens only
at this boundary. The agent-feedback path never raises — ``assemble`` returns a
:class:`RoadmapAssembly` carrying either the roadmap or the errors, so the
``submit_architecture`` tool can hand the model every problem at once.

Per-goal descriptions come from the optional ``goal_descriptions`` map the planner
supplies via ``propose_phase_plan``; a goal without an explicit description falls
back to its phase-level ``goal`` sentence. A goal with neither is reported as a
validation error rather than silently dispatched with an empty description.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.app.planning.parsing.decisions_parser import DecisionsParseError, DecisionsParser
from src.app.planning.parsing.phase_parser import PhaseParseError, PhaseParser
from src.domain.value_objects.architecture_roadmap import ArchitectureRoadmap
from src.domain.value_objects.goal import GoalSpec


@dataclass(frozen=True)
class RoadmapAssembly:
    """Outcome of an assembly attempt: exactly one of the two is meaningful."""
    roadmap: Optional[ArchitectureRoadmap]
    errors: list[str]


class RoadmapAssembler:
    def __init__(
        self,
        decisions_parser: DecisionsParser,
        phase_parser: PhaseParser,
    ) -> None:
        self._decisions_parser = decisions_parser
        self._phase_parser = phase_parser

    def assemble(self, roadmap_data: dict[str, Any] | None) -> RoadmapAssembly:
        errors: list[str] = []

        try:
            decisions = self._decisions_parser.parse_pending(roadmap_data)
        except DecisionsParseError as exc:
            decisions = []
            errors.append(f"Invalid decisions: {exc}")

        try:
            phases = self._phase_parser.parse_pending(roadmap_data)
        except PhaseParseError as exc:
            phases = []
            errors.append(f"Invalid phases: {exc}")

        descriptions: dict[str, str] = {}
        if roadmap_data:
            raw = roadmap_data.get("goal_descriptions", {})
            if isinstance(raw, dict):
                descriptions = {k: v for k, v in raw.items() if isinstance(v, str)}

        goal_specs: dict[str, GoalSpec] = {}
        for phase in phases:
            for goal_name in phase.goal_names:
                if goal_name in goal_specs:
                    continue
                description = descriptions.get(goal_name) or phase.goal
                if not description or not description.strip():
                    # Leave it out — ArchitectureRoadmap validation reports the
                    # missing description as an actionable error.
                    continue
                try:
                    goal_specs[goal_name] = GoalSpec(
                        name=goal_name, description=description, tasks=[]
                    )
                except Exception as exc:  # slug/validation failure on the goal name
                    errors.append(f"Goal '{goal_name}' is invalid: {exc}")

        errors.extend(
            ArchitectureRoadmap.validation_errors(phases, decisions, goal_specs)
        )

        if errors:
            return RoadmapAssembly(roadmap=None, errors=errors)

        return RoadmapAssembly(
            roadmap=ArchitectureRoadmap(
                phases=phases, decisions=decisions, goal_specs=goal_specs
            ),
            errors=[],
        )
