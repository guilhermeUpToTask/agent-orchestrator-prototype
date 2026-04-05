from __future__ import annotations

from dataclasses import dataclass

from src.domain.aggregates.project_plan import ProjectBrief


class BriefParseError(ValueError):
    """Raised when a project brief payload is malformed."""


@dataclass(frozen=True)
class BriefParser:
    def parse(self, artifact: dict) -> ProjectBrief:
        brief_data = artifact.get("brief", artifact)
        if not isinstance(brief_data, dict):
            raise BriefParseError("brief payload must be a JSON object")

        vision = brief_data.get("vision")
        if not isinstance(vision, str) or not vision.strip():
            raise BriefParseError("brief payload is missing a non-empty 'vision'")

        constraints = brief_data.get("constraints", [])
        open_questions = brief_data.get("open_questions", [])
        if not isinstance(constraints, list) or not all(isinstance(x, str) for x in constraints):
            raise BriefParseError("'constraints' must be a list of strings")
        if not isinstance(open_questions, list) or not all(isinstance(x, str) for x in open_questions):
            raise BriefParseError("'open_questions' must be a list of strings")

        phase_1_exit_criteria = brief_data.get("phase_1_exit_criteria", "")
        if not isinstance(phase_1_exit_criteria, str):
            raise BriefParseError("'phase_1_exit_criteria' must be a string")

        return ProjectBrief(
            vision=vision,
            constraints=constraints,
            phase_1_exit_criteria=phase_1_exit_criteria,
            open_questions=open_questions,
        )
