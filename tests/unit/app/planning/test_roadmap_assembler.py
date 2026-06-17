"""
tests/unit/app/planning/test_roadmap_assembler.py

The RoadmapAssembler is the single typed boundary between the planner's untyped
``roadmap_data`` dict and the rest of the system. It must build a valid
ArchitectureRoadmap from coherent data, fall back to the phase-level ``goal`` when
a goal has no explicit description, and return actionable errors (never raise) for
the agent-feedback path.
"""
from __future__ import annotations

from src.app.planning.parsing.decisions_parser import DecisionsParser
from src.app.planning.parsing.phase_parser import PhaseParser
from src.app.planning.parsing.spec_changes_parser import SpecChangesParser
from src.app.planning.roadmap_assembler import RoadmapAssembler


def _assembler() -> RoadmapAssembler:
    return RoadmapAssembler(
        DecisionsParser(SpecChangesParser()), PhaseParser()
    )


def _data(goal_descriptions: dict | None = None) -> dict:
    data: dict = {
        "pending_decisions": [
            {"id": "use-fastapi", "domain": "backend", "content": "Use FastAPI."}
        ],
        "pending_phases": [
            {
                "index": 0,
                "name": "Foundation",
                "goal": "Stand up the skeleton.",
                "goal_names": ["setup-db"],
                "exit_criteria": "builds",
            }
        ],
    }
    if goal_descriptions is not None:
        data["goal_descriptions"] = goal_descriptions
    return data


class TestAssemble:
    def test_builds_valid_roadmap(self):
        assembly = _assembler().assemble(_data())
        assert assembly.errors == []
        assert assembly.roadmap is not None
        assert [d.id for d in assembly.roadmap.decisions] == ["use-fastapi"]

    def test_explicit_description_wins_over_phase_goal(self):
        assembly = _assembler().assemble(
            _data({"setup-db": "Provision and migrate the database."})
        )
        assert assembly.roadmap is not None
        assert (
            assembly.roadmap.goal_specs["setup-db"].description
            == "Provision and migrate the database."
        )

    def test_falls_back_to_phase_goal_when_no_explicit_description(self):
        assembly = _assembler().assemble(_data())
        assert assembly.roadmap is not None
        assert assembly.roadmap.goal_specs["setup-db"].description == "Stand up the skeleton."

    def test_missing_any_description_is_an_error(self):
        data = _data()
        data["pending_phases"][0]["goal"] = ""  # no phase goal, no explicit desc
        assembly = _assembler().assemble(data)
        assert assembly.roadmap is None
        assert any("setup-db" in e for e in assembly.errors)

    def test_empty_data_returns_errors_not_exception(self):
        assembly = _assembler().assemble({})
        assert assembly.roadmap is None
        assert assembly.errors  # at least "must contain at least one phase"
