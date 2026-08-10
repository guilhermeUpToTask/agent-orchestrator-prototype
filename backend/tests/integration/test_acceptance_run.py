"""The cycle acceptance run, driven through a real cyclic walk.

Two properties under test, and the second is the important one:

  1. the run fires at both trigger points and its verdict is recorded;
  2. it can NEVER interfere. A failing verdict, a raising adapter, and a
     recording failure all leave promotion and publication exactly as they were.
"""

from __future__ import annotations

from dataclasses import replace
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


# ---------------------------------------------------------------------------
# Ordering: the acceptance run happens BEFORE the publication gate opens.
# Both properties below were broken by an earlier placement that ran it after.
# ---------------------------------------------------------------------------


class ActivityCapturingEnvironment(RecordingEnvironment):
    """Records what the plan's derived `activity` is while it is running."""

    def __init__(self, container_ref, plan_ref) -> None:
        super().__init__("passed")
        self.activities: list[str] = []
        self._container_ref = container_ref
        self._plan_ref = plan_ref

    def verify(self, repo, ref, spec):
        container = self._container_ref()
        plan_id = self._plan_ref()
        if container is not None and plan_id is not None:
            with container.new_unit_of_work() as uow:
                self.activities.append(uow.plans.get(plan_id).activity)
        return super().verify(repo, ref, spec)


def test_the_gate_is_not_open_while_the_acceptance_run_executes(tmp_path, monkeypatch):
    """The race this ordering closes: booting an application takes minutes, so
    a gate open during the run lets an operator record a disposition against a
    verdict that does not exist yet."""
    box: dict[str, object] = {}
    environment = ActivityCapturingEnvironment(
        lambda: box.get("container"), lambda: box.get("plan_id")
    )

    original = environment.verify

    def verify(repo, ref, spec):
        return original(repo, ref, spec)

    environment.verify = verify  # type: ignore[method-assign]

    walk = drive_cycle_to_publication(
        tmp_path, monkeypatch, publish=False, environment=environment,
        on_ready=lambda container, plan_id: box.update(
            container=container, plan_id=plan_id
        ),
    )

    pre_publication = [a for a in environment.activities if a == "cycle_verification"]
    assert pre_publication, (
        "the pre-publication run must execute while the plan reports "
        f"cycle_verification; saw {environment.activities}"
    )
    assert not any(a.startswith("review:") for a in environment.activities), (
        "the publication gate must not be open while the run executes"
    )
    # And the gate does open afterwards.
    with walk.container.new_unit_of_work() as uow:
        assert uow.plans.get(walk.plan_id).review_gate is not None


def test_the_pre_publication_run_fills_the_cycle_verification_slot(tmp_path, monkeypatch):
    """`Plan.activity` checks review_gate BEFORE falling through to
    cycle_verification, so a run placed after the gate leaves that label naming
    a slot with nothing in it. Nothing was added to the domain to fix this —
    running it in the right window makes the EXISTING derivation produce it."""
    box: dict[str, object] = {}
    environment = ActivityCapturingEnvironment(
        lambda: box.get("container"), lambda: box.get("plan_id")
    )

    drive_cycle_to_publication(
        tmp_path, monkeypatch, publish=False, environment=environment,
        on_ready=lambda container, plan_id: box.update(
            container=container, plan_id=plan_id
        ),
    )

    assert "cycle_verification" in environment.activities


def test_the_pre_publication_run_happens_once_per_cycle(tmp_path, monkeypatch):
    """The ledger is the idempotency key: one pre_publication row per cycle
    means done. No in-flight state is persisted anywhere, least of all in the
    aggregate."""
    walk = _walk(tmp_path, monkeypatch, RecordingEnvironment("passed"))

    pre_publication = [r for r in _runs(walk) if r.trigger == "pre_publication"]

    assert len(pre_publication) == 1


# ---------------------------------------------------------------------------
# Regression: an adapter that records NOTHING must not loop the tick.
#
# The first version of the pre-gate ordering returned CONTINUE and let the next
# tick open the publication gate, keyed on "is there a pre_publication ledger
# row". A `skipped` or raising adapter records no row, so that guard never
# became true and the tick re-ran the acceptance forever. Acceptance and
# gate-opening therefore happen in the SAME tick.
#
# The tests above catch this only by side effect (the walk stops settling), so
# it is stated here explicitly — a ledger-only guard reads like an obvious
# simplification to anyone who has not hit this.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["skipped", "raises"])
def test_an_adapter_that_records_nothing_still_settles_the_gate_once(
    tmp_path, monkeypatch, mode
):
    environment = RecordingEnvironment(mode)

    walk = _walk(tmp_path, monkeypatch, environment)

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    assert plan.review_gate is not None, (
        f"a {mode!r} adapter records no ledger row; the gate must still open"
    )
    assert plan.review_gate.subject_type.value == "cycle_completion"
    assert plan.review_gate.unresolved
    assert _runs(walk) == []  # nothing recorded, by design


@pytest.mark.parametrize("mode", ["skipped", "raises", "passed"])
def test_the_acceptance_run_is_attempted_a_bounded_number_of_times(
    tmp_path, monkeypatch, mode
):
    """The loop this locks was unbounded: `verify()` was called on every tick
    forever. Bounding it by a small constant rather than an exact count keeps
    the test honest about goal-merge triggers varying with decomposition."""
    environment = RecordingEnvironment(mode)

    _walk(tmp_path, monkeypatch, environment)

    assert len(environment.calls) <= 8, (
        f"verify() was called {len(environment.calls)} times for a single cycle; "
        "the tick is re-running the acceptance instead of settling"
    )


def test_a_further_tick_does_not_reopen_or_re_run_anything(tmp_path, monkeypatch):
    """Once the gate is open the plan is parked. A later tick must not run the
    acceptance again — which is what the same-tick ordering guarantees, and
    what a ledger-only guard would get wrong for a non-recording adapter."""
    import asyncio

    from agent_orchestrator.app.use_cases.run_worker import drive_plan

    environment = RecordingEnvironment("skipped")
    walk = _walk(tmp_path, monkeypatch, environment)
    calls_after_walk = len(environment.calls)

    async def tick():
        return await drive_plan(
            walk.plan_id,
            walk.container.new_unit_of_work(),
            replace(walk.container.execution_services, environment=environment),
            "worker-2",
        )

    signal, progressed = asyncio.run(tick())

    assert len(environment.calls) == calls_after_walk, (
        "a tick against a plan already parked at its publication gate re-ran "
        "the acceptance"
    )
    assert progressed == 0
    assert signal in {"paused", "not_ready"}
