from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Callable, Optional

from src.app.planning.parsing.spec_changes_parser import SpecChangesParseError, SpecChangesParser
from src.domain.aggregates.planner_session import PlannerSession
from src.domain.ports.planner import PlannerTool
from src.domain.ports.project_state import DecisionEntry


def build_propose_decision_tool(
    session: PlannerSession,
    session_save: Callable[[PlannerSession], None],
    spec_changes_parser: SpecChangesParser,
    event_hook: Optional[Callable[[str, dict], None]] = None,
    strict_schema: bool = False,
) -> PlannerTool:
    def propose_decision_handler(inp: dict) -> str:
        try:
            spec_changes = spec_changes_parser.parse(inp.get("spec_changes_json"))
        except SpecChangesParseError as exc:
            return json.dumps({"proposed": False, "error": str(exc)})

        entry = DecisionEntry(
            id=inp["id"],
            date=inp.get("date", str(date.today())),
            status="active",
            domain=inp["domain"],
            feature_tag=inp.get("feature_tag", ""),
            content=inp["content"],
            spec_changes=spec_changes,
        )
        decisions = session.roadmap_data.get("pending_decisions", []) if session.roadmap_data else []
        decisions.append(asdict(entry))
        session.record_roadmap_candidate({"pending_decisions": decisions})
        session_save(session)

        if event_hook:
            event_hook("decision_proposed", {"id": entry.id, "domain": entry.domain})

        return json.dumps({"proposed": True, "id": entry.id})

    if strict_schema:
        input_schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Short slug, e.g. 'use-fastapi'"},
                "domain": {"type": "string", "description": "e.g. 'backend' or 'infra'"},
                "feature_tag": {"type": "string"},
                "content": {"type": "string", "description": "Markdown explanation"},
                "spec_changes_json": {
                    "type": "string",
                    "description": (
                        "Optional JSON string for spec changes: "
                        '{"add_required": [], "add_forbidden": [], '
                        '"remove_required": [], "remove_forbidden": []}'
                    ),
                },
            },
            "required": ["id", "domain", "content"],
        }
    else:
        input_schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "domain": {"type": "string"},
                "feature_tag": {"type": "string"},
                "content": {"type": "string"},
                "spec_changes_json": {"type": "string"},
            },
            "required": ["id", "domain", "content"],
        }

    return PlannerTool(
        name="propose_decision",
        description="Propose an architectural decision for approval.",
        input_schema=input_schema,
        handler=propose_decision_handler,
    )
