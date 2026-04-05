from __future__ import annotations

import json
from typing import Callable, Optional

from src.domain.aggregates.planner_session import PlannerSession
from src.domain.ports.planner import PlannerTool
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort


def build_read_project_brief_tool(plan_repo: ProjectPlanRepositoryPort) -> PlannerTool:
    def read_brief_handler(_: dict) -> str:
        plan = plan_repo.get()
        if plan and plan.brief:
            brief = plan.brief
            return json.dumps(
                {
                    "vision": brief.vision,
                    "constraints": brief.constraints,
                    "phase_1_exit_criteria": brief.phase_1_exit_criteria,
                    "open_questions": brief.open_questions,
                }
            )
        return json.dumps({"error": "No brief found"})

    return PlannerTool(
        name="read_project_brief",
        description="Read the approved project brief.",
        input_schema={"type": "object", "properties": {}},
        handler=read_brief_handler,
    )


def build_propose_phase_plan_tool(
    session: PlannerSession,
    session_save: Callable[[PlannerSession], None],
    event_hook: Optional[Callable[[str, dict], None]] = None,
) -> PlannerTool:
    def propose_phase_plan_handler(inp: dict) -> str:
        phases_json = inp.get("phases_json", "[]")
        try:
            phases_data = json.loads(phases_json)
            if not isinstance(phases_data, list):
                raise ValueError("phases_json must be a JSON array of objects.")

            required = {"index", "name", "goal", "goal_names", "exit_criteria"}
            for i, phase in enumerate(phases_data):
                missing = required - set(phase.keys())
                if missing:
                    raise ValueError(f"Phase at index {i} is missing required keys: {missing}")

            session.record_roadmap_candidate({"pending_phases": phases_data})
            session_save(session)

            if event_hook:
                for phase in phases_data:
                    event_hook(
                        "phase_proposed",
                        {
                            "name": phase.get("name", ""),
                            "goal_names": phase.get("goal_names", []),
                        },
                    )

            return json.dumps({"proposed": True, "phase_count": len(phases_data)})
        except Exception as exc:
            return json.dumps({"proposed": False, "error": str(exc)})

    return PlannerTool(
        name="propose_phase_plan",
        description="Propose the phase plan for approval.",
        input_schema={
            "type": "object",
            "properties": {
                "phases_json": {
                    "type": "string",
                    "description": (
                        "JSON string array of phases. Format: "
                        '[{"index": 0, "name": "Foundation", '
                        '"goal": "Setup base", "goal_names": ["setup-db"], '
                        '"exit_criteria": "DB is up"}]'
                    ),
                }
            },
            "required": ["phases_json"],
        },
        handler=propose_phase_plan_handler,
    )


def build_submit_architecture_tool(session: PlannerSession) -> PlannerTool:
    def submit_architecture_handler(_: dict) -> str:
        data = session.roadmap_data or {}
        decisions = data.get("pending_decisions", [])
        phases = data.get("pending_phases", [])
        if not decisions:
            return json.dumps({"accepted": False, "error": "No decisions proposed"})
        if not phases:
            return json.dumps({"accepted": False, "error": "No phases proposed"})
        return json.dumps({"accepted": True})

    return PlannerTool(
        name="submit_architecture",
        description="Submit the architecture for approval. Requires at least one decision and one phase.",
        input_schema={"type": "object", "properties": {}},
        handler=submit_architecture_handler,
    )
