"""
tests/unit/app/planning/test_submit_architecture_tool.py

submit_architecture is the planner's terminal tool and its validation feedback
loop: on an invalid roadmap it must return {accepted: false, errors: [...]} so the
agent can fix and resubmit, and {accepted: true} only when the roadmap is whole.
"""
from __future__ import annotations

import json

from src.app.planning.parsing.decisions_parser import DecisionsParser
from src.app.planning.parsing.phase_parser import PhaseParser
from src.app.planning.parsing.spec_changes_parser import SpecChangesParser
from src.app.planning.roadmap_assembler import RoadmapAssembler
from src.app.planning.tools.architecture_tools import (
    build_propose_phase_plan_tool,
    build_submit_architecture_tool,
)
from src.app.planning.tools.decision_tools import build_propose_decision_tool
from src.domain.aggregates.planner_session import PlannerMode, PlannerSession


def _session() -> PlannerSession:
    session = PlannerSession.create("test", mode=PlannerMode.ARCHITECTURE)
    session.start()
    return session


def _assembler() -> RoadmapAssembler:
    return RoadmapAssembler(DecisionsParser(SpecChangesParser()), PhaseParser())


def _propose_decision(session: PlannerSession) -> None:
    build_propose_decision_tool(
        session=session,
        session_save=lambda _s: None,
        spec_changes_parser=SpecChangesParser(),
    ).handler({"id": "use-fastapi", "domain": "backend", "content": "Use FastAPI."})


def _propose_phase(session: PlannerSession, goal_descriptions: dict | None = None) -> None:
    phase = {
        "index": 0,
        "name": "Foundation",
        "goal": "Stand up the skeleton.",
        "goal_names": ["setup-db"],
        "exit_criteria": "builds",
    }
    if goal_descriptions is not None:
        phase["goal_descriptions"] = goal_descriptions
    build_propose_phase_plan_tool(
        session=session, session_save=lambda _s: None
    ).handler({"phases_json": json.dumps([phase])})


class TestSubmitArchitecture:
    def test_accepts_complete_roadmap(self):
        session = _session()
        _propose_decision(session)
        _propose_phase(session)

        result = json.loads(
            build_submit_architecture_tool(session, _assembler()).handler({})
        )
        assert result == {"accepted": True}

    def test_rejects_when_no_decision(self):
        session = _session()
        _propose_phase(session)

        result = json.loads(
            build_submit_architecture_tool(session, _assembler()).handler({})
        )
        assert result["accepted"] is False
        assert any("decision" in e.lower() for e in result["errors"])

    def test_rejects_when_no_phase(self):
        session = _session()
        _propose_decision(session)

        result = json.loads(
            build_submit_architecture_tool(session, _assembler()).handler({})
        )
        assert result["accepted"] is False
        assert result["errors"]

    def test_terminal_flag_is_set(self):
        tool = build_submit_architecture_tool(_session(), _assembler())
        assert tool.terminal is True
