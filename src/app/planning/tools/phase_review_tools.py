from __future__ import annotations

import json
from typing import Callable

from src.domain.aggregates.planner_session import PlannerSession
from src.domain.aggregates.project_plan import ProjectPlan
from src.domain.ports.planner import PlannerTool
from src.domain.repositories.goal_repository import GoalRepositoryPort


def build_read_phase_summary_tool(plan: ProjectPlan, goal_repo: GoalRepositoryPort) -> PlannerTool:
    def read_phase_summary_handler(_: dict) -> str:
        current_phase = plan.current_phase()
        if not current_phase:
            return json.dumps({"error": "No active phase"})

        goals = []
        for goal_name in current_phase.goal_names:
            goal = goal_repo.get_by_name(goal_name)
            if goal:
                goals.append(
                    {
                        "name": goal.name,
                        "status": goal.status.value,
                        "description": goal.description,
                    }
                )

        return json.dumps(
            {
                "phase_name": current_phase.name,
                "phase_goal": current_phase.goal,
                "goals": goals,
                "goal_names": current_phase.goal_names,
            }
        )

    return PlannerTool(
        name="read_phase_summary",
        description="Read summary of the completed phase.",
        input_schema={"type": "object", "properties": {}},
        handler=read_phase_summary_handler,
    )


def build_propose_next_phase_tool(
    session: PlannerSession,
    session_save: Callable[[PlannerSession], None],
    default_index: int,
) -> PlannerTool:
    def propose_next_phase_handler(inp: dict) -> str:
        phase_data = {
            "index": inp.get("index", default_index),
            "name": inp.get("name", ""),
            "goal": inp.get("goal", ""),
            "exit_criteria": inp.get("exit_criteria", ""),
        }
        session.record_roadmap_candidate({"next_phase": phase_data})
        session_save(session)
        return json.dumps({"proposed": True})

    return PlannerTool(
        name="propose_next_phase",
        description="Propose the next phase for approval.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "goal": {"type": "string"},
                "exit_criteria": {"type": "string"},
            },
            "required": ["name", "goal", "exit_criteria"],
        },
        handler=propose_next_phase_handler,
    )


def build_submit_review_tool(
    session: PlannerSession,
    session_save: Callable[[PlannerSession], None],
) -> PlannerTool:
    def submit_review_handler(inp: dict) -> str:
        lessons = inp.get("lessons", "")
        architecture_summary = inp.get("architecture_summary", "")
        session.record_roadmap_candidate(
            {
                "lessons": lessons,
                "architecture_summary": architecture_summary,
            }
        )
        session_save(session)
        return json.dumps({"accepted": True})

    return PlannerTool(
        name="submit_review",
        description="Submit the phase review with lessons and architecture update.",
        input_schema={
            "type": "object",
            "properties": {
                "lessons": {"type": "string"},
                "architecture_summary": {"type": "string"},
            },
            "required": ["lessons", "architecture_summary"],
        },
        handler=submit_review_handler,
    )
