"""
tests/unit/domain/value_objects/test_architecture_roadmap.py

Structural validation of the typed ArchitectureRoadmap — the output contract of
the ARCHITECTURE phase. These guard the feedback loop the planner relies on to
converge: every rule that fires here is a message the agent gets back from
submit_architecture.
"""
from __future__ import annotations

import pytest

from src.domain.aggregates.project_plan import Phase, PhaseStatus
from src.domain.ports.project_state import DecisionEntry
from src.domain.value_objects.architecture_roadmap import (
    ArchitectureRoadmap,
    RoadmapValidationError,
)
from src.domain.value_objects.goal import GoalSpec


def _phase(index: int, goal_names: list[str], goal: str = "do the thing") -> Phase:
    return Phase(
        index=index,
        name=f"Phase {index}",
        goal=goal,
        goal_names=goal_names,
        status=PhaseStatus.PLANNED,
        lessons="",
        exit_criteria="done",
    )


def _decision(decision_id: str = "d1") -> DecisionEntry:
    return DecisionEntry(
        id=decision_id, date="2026-01-01", status="active",
        domain="backend", feature_tag="", content="Use X.",
    )


def _spec(name: str) -> GoalSpec:
    return GoalSpec(name=name, description=f"Build {name}.", tasks=[])


class TestValid:
    def test_constructs_when_coherent(self):
        roadmap = ArchitectureRoadmap(
            phases=[_phase(0, ["alpha"]), _phase(1, ["beta"])],
            decisions=[_decision()],
            goal_specs={"alpha": _spec("alpha"), "beta": _spec("beta")},
        )
        assert [p.index for p in roadmap.phases] == [0, 1]
        assert roadmap.goal_specs["alpha"].description == "Build alpha."


class TestValidation:
    def test_empty_phases_rejected(self):
        with pytest.raises(RoadmapValidationError):
            ArchitectureRoadmap(phases=[], decisions=[_decision()], goal_specs={})

    def test_missing_goal_spec_rejected(self):
        errors = ArchitectureRoadmap.validation_errors(
            [_phase(0, ["alpha"])], [_decision()], {}
        )
        assert any("alpha" in e and "no description" in e for e in errors)

    def test_non_contiguous_indices_rejected(self):
        errors = ArchitectureRoadmap.validation_errors(
            [_phase(0, ["alpha"]), _phase(2, ["beta"])],
            [_decision()],
            {"alpha": _spec("alpha"), "beta": _spec("beta")},
        )
        assert any("contiguous" in e for e in errors)

    def test_duplicate_goal_names_rejected(self):
        errors = ArchitectureRoadmap.validation_errors(
            [_phase(0, ["alpha"]), _phase(1, ["alpha"])],
            [_decision()],
            {"alpha": _spec("alpha")},
        )
        assert any("Duplicate goal names" in e for e in errors)

    def test_phase_without_goals_rejected(self):
        errors = ArchitectureRoadmap.validation_errors(
            [_phase(0, [])], [_decision()], {}
        )
        assert any("dispatches no goals" in e for e in errors)
