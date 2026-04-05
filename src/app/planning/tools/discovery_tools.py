from __future__ import annotations

import json
from typing import Callable

from src.domain.aggregates.planner_session import PlannerSession
from src.domain.ports.planner import PlannerTool


def build_ask_question_tool() -> PlannerTool:
    """Tool used by interactive discovery runtime to ask clarifying questions."""

    def ask_question_handler(inp: dict) -> str:
        question = inp.get("question", "")
        return json.dumps({"asked": True, "question": question})

    return PlannerTool(
        name="ask_question",
        description="Ask the user a clarifying question about the project.",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to ask the user"}
            },
            "required": ["question"],
        },
        handler=ask_question_handler,
    )


def build_submit_project_brief_tool(
    session: PlannerSession,
    session_save: Callable[[PlannerSession], None],
) -> PlannerTool:
    """Accept and validate a final discovery brief payload."""

    def submit_brief_handler(inp: dict) -> str:
        brief_json = inp.get("brief_json", "")
        try:
            data = json.loads(brief_json)
            required_keys = {"vision", "constraints", "phase_1_exit_criteria", "open_questions"}
            missing = required_keys - set(data.keys())
            if missing:
                raise ValueError(
                    f"Missing required keys in JSON: {missing}. You must use the exact keys requested."
                )
            session.record_roadmap_candidate({"brief": data})
            session_save(session)
            return json.dumps({"accepted": True})
        except Exception as exc:
            return json.dumps({"accepted": False, "error": str(exc)})

    return PlannerTool(
        name="submit_project_brief",
        description="Submit the final project brief after gathering requirements.",
        input_schema={
            "type": "object",
            "properties": {
                "brief_json": {
                    "type": "string",
                    "description": (
                        "JSON string with brief data. MUST strictly follow this format: "
                        '{"vision": "High level summary", '
                        '"constraints": ["Limits"], '
                        '"phase_1_exit_criteria": "What defines MVP", '
                        '"open_questions": ["Pending items"]}'
                    ),
                }
            },
            "required": ["brief_json"],
        },
        handler=submit_brief_handler,
    )
