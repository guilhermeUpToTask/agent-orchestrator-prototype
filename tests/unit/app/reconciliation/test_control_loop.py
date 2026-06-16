"""
Unit tests for the federated reconcile framework: ReconcilerScheduler isolation
and backoff, and the PhaseDispatchReconciler level-triggered divergence check.
"""
from unittest.mock import MagicMock

from src.app.reconciliation import (
    ControlLoop,
    PhaseDispatchReconciler,
    ReconcilerScheduler,
)
from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectPlan,
    ProjectPlanStatus,
)


class _RecordingLoop(ControlLoop):
    def __init__(self, name, *, fail=False):
        self.name = name
        self.interval_seconds = 1
        self.calls = 0
        self._fail = fail

    def reconcile_once(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")


def test_scheduler_runs_each_loop_once_and_isolates_failures():
    good = _RecordingLoop("good")
    bad = _RecordingLoop("bad", fail=True)
    other = _RecordingLoop("other")
    sched = ReconcilerScheduler([good, bad, other])

    sched.run_once()  # must not raise despite bad loop

    assert good.calls == 1
    assert bad.calls == 1
    assert other.calls == 1  # failure in 'bad' did not stop 'other'


def test_scheduler_backs_off_failing_loop():
    bad = _RecordingLoop("bad", fail=True)
    sched = ReconcilerScheduler([bad], max_backoff_seconds=100)
    sched.run_once()
    sched.run_once()
    # backoff grew (interval=1 → 2 → ...), capped at max
    assert sched._backoff["bad"] >= 2


def test_single_writer_guard_skips_when_not_leader():
    loop = _RecordingLoop("guarded")
    sched = ReconcilerScheduler([loop], single_writer_guard=lambda _name: False)
    sched.run_once()
    assert loop.calls == 0  # guard denied → loop never ran


def _phase_active_plan(goal_names):
    return ProjectPlan(
        plan_id="p",
        status=ProjectPlanStatus.PHASE_ACTIVE,
        current_phase_index=0,
        phases=[
            Phase(
                index=0,
                name="Foundation",
                goal="g",
                goal_names=list(goal_names),
                status=PhaseStatus.ACTIVE,
                exit_criteria="x",
                lessons="",
            )
        ],
    )


def _goal(name):
    g = MagicMock()
    g.name = name
    return g


def test_phase_dispatch_resumes_on_divergence():
    plan_repo = MagicMock()
    plan_repo.load.return_value = _phase_active_plan(["a", "b"])
    goal_repo = MagicMock()
    goal_repo.list_all.return_value = [_goal("a")]  # 'b' missing → divergence
    resume = MagicMock(return_value=MagicMock(goals_dispatched=["goal-b"], goals_failed=[]))

    loop = PhaseDispatchReconciler(plan_repo, goal_repo, resume)
    loop.reconcile_once()

    resume.assert_called_once()


def test_phase_dispatch_noop_when_converged():
    plan_repo = MagicMock()
    plan_repo.load.return_value = _phase_active_plan(["a", "b"])
    goal_repo = MagicMock()
    goal_repo.list_all.return_value = [_goal("a"), _goal("b")]  # all present
    resume = MagicMock()

    loop = PhaseDispatchReconciler(plan_repo, goal_repo, resume)
    loop.reconcile_once()

    resume.assert_not_called()


def test_phase_dispatch_noop_when_not_phase_active():
    plan_repo = MagicMock()
    plan_repo.load.return_value = ProjectPlan(
        plan_id="p", status=ProjectPlanStatus.ARCHITECTURE
    )
    goal_repo = MagicMock()
    resume = MagicMock()

    loop = PhaseDispatchReconciler(plan_repo, goal_repo, resume)
    loop.reconcile_once()

    resume.assert_not_called()
