from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.domain.aggregates.project_plan import Phase, PhaseStatus


class PhaseParseError(ValueError):
    """Raised when phase payloads are malformed."""


@dataclass(frozen=True)
class PhaseParser:
    def parse_pending(self, roadmap_data: dict[str, Any] | None) -> list[Phase]:
        if not roadmap_data:
            return []
        phases_data = roadmap_data.get("pending_phases", [])
        if not isinstance(phases_data, list):
            raise PhaseParseError("'pending_phases' must be a list")

        phases: list[Phase] = []
        for idx, phase in enumerate(phases_data):
            if not isinstance(phase, dict):
                raise PhaseParseError(f"pending_phases[{idx}] must be an object")
            goal_names = phase.get("goal_names", [])
            if not isinstance(goal_names, list) or not all(isinstance(g, str) for g in goal_names):
                raise PhaseParseError(f"pending_phases[{idx}].goal_names must be a list of strings")
            phases.append(
                Phase(
                    index=phase.get("index", 0),
                    name=phase.get("name", ""),
                    goal=phase.get("goal", ""),
                    goal_names=goal_names,
                    status=PhaseStatus.PLANNED,
                    lessons="",
                    exit_criteria=phase.get("exit_criteria", ""),
                )
            )
        return phases

    def parse_next(self, roadmap_data: dict[str, Any] | None) -> Optional[Phase]:
        if not roadmap_data:
            return None

        phase_data = roadmap_data.get("next_phase")
        if not phase_data:
            return None
        if not isinstance(phase_data, dict):
            raise PhaseParseError("'next_phase' must be an object")

        return Phase(
            index=phase_data.get("index", 0),
            name=phase_data.get("name", ""),
            goal=phase_data.get("goal", ""),
            goal_names=[],
            status=PhaseStatus.PLANNED,
            lessons="",
            exit_criteria=phase_data.get("exit_criteria", ""),
        )
