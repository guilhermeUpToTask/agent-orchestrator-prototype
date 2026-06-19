from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.domain.aggregates.planner_session import PlannerSession
from src.domain.ports.planner import PlannerTool
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort

if TYPE_CHECKING:
    from src.app.planning.roadmap_assembler import RoadmapAssembler


def build_read_project_brief_tool(plan_repo: ProjectPlanRepositoryPort) -> PlannerTool:
    def read_brief_handler(_: dict[str, Any]) -> str:
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
    event_hook: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> PlannerTool:
    def propose_phase_plan_handler(inp: dict[str, Any]) -> str:
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

            # Collect optional per-goal descriptions so each dispatched goal can
            # carry its own description instead of inheriting the phase sentence.
            descriptions: dict[str, str] = dict(
                (session.roadmap_data or {}).get("goal_descriptions", {})
            )
            for phase in phases_data:
                phase_descs = phase.get("goal_descriptions", {})
                if isinstance(phase_descs, dict):
                    descriptions.update(
                        {k: v for k, v in phase_descs.items() if isinstance(v, str)}
                    )

            session.record_roadmap_candidate(
                {"pending_phases": phases_data, "goal_descriptions": descriptions}
            )
            session_save(session)

            if event_hook:
                for phase in phases_data:
                    goal_names = phase.get("goal_names", [])
                    event_hook(
                        "phase_proposed",
                        {
                            "name": phase.get("name", ""),
                            "goal_names": goal_names,
                            "goal_descriptions": {
                                name: descriptions.get(name, phase.get("goal", ""))
                                for name in goal_names
                            },
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
                        '"exit_criteria": "DB is up", '
                        '"goal_descriptions": {"setup-db": "Provision and '
                        'migrate the primary database."}}]. '
                        "goal_descriptions is optional; give each goal its own "
                        "description, otherwise it inherits the phase 'goal'."
                    ),
                }
            },
            "required": ["phases_json"],
        },
        handler=propose_phase_plan_handler,
    )


def build_submit_architecture_tool(
    session: PlannerSession,
    assembler: "RoadmapAssembler",
) -> PlannerTool:
    def submit_architecture_handler(_: dict[str, Any]) -> str:
        assembly = assembler.assemble(session.roadmap_data)
        errors = list(assembly.errors)
        if assembly.roadmap is not None and not assembly.roadmap.decisions:
            errors.append("Propose at least one architectural decision.")
        if errors:
            return json.dumps({"accepted": False, "errors": errors})
        return json.dumps({"accepted": True})

    return PlannerTool(
        name="submit_architecture",
        description=(
            "Submit the architecture for approval. Requires at least one "
            "decision and one phase, contiguous phase indices, and a "
            "description for every goal. If the roadmap is invalid this returns "
            "{accepted: false, errors: [...]} so you can fix and resubmit."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=submit_architecture_handler,
        terminal=True,
    )
