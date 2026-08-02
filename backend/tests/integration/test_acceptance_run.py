"""The cycle acceptance run, driven through a real cyclic walk.

Two properties under test, and the second is the important one:

  1. the run fires at both trigger points and its verdict is recorded;
  2. it can NEVER interfere. A failing verdict, a raising adapter, and a
     recording failure all leave promotion and publication exactly as they were.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.app.environment_port import (
    AcceptanceVerdict,
    EnvironmentSpec,
    ProjectEnvironment,
)
from agent_orchestrator.domain.entities.planning_artifacts import OutputDisposition
from tests.integration.cyclic_walk import drive_cycle_to_publication

pytestmark = pytest.mark.integration


class RecordingEnvironment:
    """A scriptable ProjectEnvironment. `mode` selects what it does."""

    def __init__(self, mode: str = "passed") -> None:
        self.mode = mode
        self.calls: list[tuple[Path, str]] = []

    def verify(
        self, repo: Path, ref: str, spec: EnvironmentSpec | None
    ) -> AcceptanceVerdict:
        self.calls.append((repo, ref))
        if self.mode == "raises":
            raise RuntimeError("adapter exploded")
        if self.mode == "skipped":
            return AcceptanceVerdict(outcome="skipped", summary="nothing configured")
        return AcceptanceVerdict(
            outcome=self.mode,
            summary=f"scenario {self.mode}",
            detail="GET / returned 500" if self.mode == "failed" else "",
            duration_seconds=1.5,
        )


def _walk(tmp_path, monkeypatch, environment):
    return drive_cycle_to_publication(
        tmp_path,
        monkeypatch,
        publish=False,
        environment=environment,
    )


def _runs(walk):
    with walk.container.new_unit_of_work() as uow:
        return uow.acceptance_runs.list_for_cycle(walk.plan_id, walk.cycle_id)


def test_it_satisfies_the_protocol():
    assert isinstance(RecordingEnvironment(), ProjectEnvironment)


def test_a_verdict_is_recorded_at_both_trigger_points(tmp_path, monkeypatch):
    environment = RecordingEnvironment("passed")

    walk = _walk(tmp_path, monkeypatch, environment)

    runs = _runs(walk)
    triggers = [run.trigger for run in runs]
    assert "goal_merge" in triggers
    assert "pre_publication" in triggers
    assert all(run.outcome == "passed" for run in runs)
    # It boots the CYCLE branch — the assembled tree, not one goal's work.
    assert all(ref == f"cycle/{walk.cycle_id}" for _, ref in environment.calls)


def test_a_pre_publication_run_names_no_goal(tmp_path, monkeypatch):
    """It observes the whole cycle, so attributing it to one goal would lie."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("passed"))

    pre_publication = [r for r in _runs(walk) if r.trigger == "pre_publication"]

    assert pre_publication
    assert all(run.goal_id is None for run in pre_publication)


def test_a_goal_merge_run_names_its_goal(tmp_path, monkeypatch):
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("passed"))

    goal_merges = [r for r in _runs(walk) if r.trigger == "goal_merge"]

    assert goal_merges
    assert all(run.goal_id is not None for run in goal_merges)


def test_a_failing_verdict_does_not_stop_publication(tmp_path, monkeypatch):
    """The design's central claim: advisory means advisory. A flaky acceptance
    run that could withhold publication would cost more trust than it earns."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("failed"))

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)

    assert plan.review_gate is not None  # the publication gate opened anyway
    assert plan.review_gate.subject_type.value == "cycle_completion"
    assert [r.outcome for r in _runs(walk)] == ["failed"] * len(_runs(walk))


def test_a_failing_verdict_does_not_stop_goal_promotion(tmp_path, monkeypatch):
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("failed"))

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    cycle = next(c for c in plan.cycles if c.id == walk.cycle_id)

    assert cycle.evidence_refs  # the goal still merged
    with walk.container.new_unit_of_work() as uow:
        assert uow.promotions.list_for_cycle(walk.plan_id, walk.cycle_id)


def test_an_adapter_that_raises_is_swallowed(tmp_path, monkeypatch):
    """`verify` is contracted not to raise; a third-party adapter is exactly
    the thing that will. It must not take the promotion down with it."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("raises"))

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)

    assert plan.review_gate is not None
    assert _runs(walk) == []  # nothing recorded, nothing broken


def test_a_skipped_verdict_records_nothing(tmp_path, monkeypatch):
    """One row per goal merge saying "nobody asked" is noise, not evidence."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("skipped"))

    assert _runs(walk) == []


def test_no_environment_configured_changes_nothing(tmp_path, monkeypatch):
    """The default install: NoEnvironment is wired, and the walk is identical."""
    walk = drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    assert plan.review_gate is not None
    assert _runs(walk) == []


def test_the_verdict_survives_publication(tmp_path, monkeypatch):
    """An operator reads it at the gate, and it stays readable afterwards."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("passed"))
    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    gate = plan.review_gate
    assert gate is not None

    from agent_orchestrator.app.use_cases.cyclic_planning import record_output_disposition

    record_output_disposition(
        walk.plan_id,
        gate.id,
        gate.subject_revision,
        OutputDisposition.RETAIN_BRANCH,
        f"cycle/{walk.cycle_id}",
        walk.container.new_unit_of_work(),
        walk.container.clock,
    )

    with walk.container.new_unit_of_work() as uow:
        latest = uow.acceptance_runs.latest_for_cycle(walk.plan_id, walk.cycle_id)
    assert latest is not None
    assert latest.outcome == "passed"
