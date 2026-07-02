"""Tests for create_plan / apply_edit / control use cases."""

import sys, os, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.value_objects.tasks_vos import Status
from domain.errors.planning_errors import EmptyPlanError, InvalidEditError
from domain.errors.tasks_errors import StaleVersionError, GoalAlreadyRunningError

from application.use_cases.create_plan import create_plan
from application.use_cases.apply_edit import (
    apply_edit,
    AddTask,
    RemoveTask,
    ReorderTasks,
    EditTaskRequirements,
)
from application.use_cases.control import resume_from_review
from application.testing.fakes import (
    InMemoryPlanRepository,
    InMemoryOutbox,
    InMemoryUnitOfWork,
)


def uow_with(plan=None):
    repo = InMemoryPlanRepository()
    if plan:
        repo.add(plan)
    return InMemoryUnitOfWork(repo, InMemoryOutbox()), repo


# ---- create_plan ----
def test_create_plan_returns_id():
    uow, repo = uow_with()
    pid = create_plan("build a REST API", "req-1", uow)
    assert pid and repo.get(pid).brief == "build a REST API"


def test_create_plan_idempotent_on_request_id():
    uow, repo = uow_with()
    pid1 = create_plan("build x", "req-1", uow)
    pid2 = create_plan("build x", "req-1", uow)  # same request_id
    assert pid1 == pid2  # NOT a duplicate
    assert len([p for p in repo._store]) == 1


def test_create_plan_different_requests_make_different_plans():
    uow, repo = uow_with()
    pid1 = create_plan("build x", "req-1", uow)
    pid2 = create_plan("build x", "req-2", uow)
    assert pid1 != pid2


def test_create_plan_empty_brief_raises():
    uow, _ = uow_with()
    with pytest.raises(EmptyPlanError):
        create_plan("   ", "req-1", uow)


# ---- apply_edit ----
def _plan_with_goal(status=Status.PENDING):
    g = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        status=status,
        tasks=[
            Task(id="t0", name="t0", position=0, description=""),
            Task(id="t1", name="t1", position=1, description=""),
        ],
    )
    return Plan(id="p1", brief="b", phase=PlanPhase.AWAITING_REVIEW, goals=[g])


def test_apply_edit_add_task_bumps_version():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    apply_edit(
        "p1", AddTask("g1", Task(id="tX", name="tX", position=9, description="")), uow
    )
    saved = repo.get("p1")
    assert any(t.id == "tX" for t in saved.goals[0].tasks)
    assert saved.version == plan.version + 1


def test_apply_edit_remove_task():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    apply_edit("p1", RemoveTask("g1", "t0"), uow)
    assert all(t.id != "t0" for t in repo.get("p1").goals[0].tasks)


def test_apply_edit_reorder():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    apply_edit("p1", ReorderTasks("g1", ["t1", "t0"]), uow)
    saved = sorted(repo.get("p1").goals[0].tasks, key=lambda t: t.position)
    assert [t.id for t in saved] == ["t1", "t0"]


def test_apply_edit_on_running_goal_rejected():
    plan = _plan_with_goal(status=Status.RUNNING)
    uow, _ = uow_with(plan)
    with pytest.raises(GoalAlreadyRunningError):
        apply_edit("p1", RemoveTask("g1", "t0"), uow)


def test_apply_edit_requirements_does_not_rematch():
    plan = _plan_with_goal()
    plan.goals[0].tasks[0].agent_id = "bound-agent"  # already bound
    uow, repo = uow_with(plan)
    apply_edit("p1", EditTaskRequirements("g1", "t0", ["new-cap"]), uow)
    saved = repo.get("p1").goals[0].tasks[0]
    assert saved.required_capabilities == ["new-cap"]
    assert saved.agent_id == "bound-agent"  # binding NOT auto-changed (snapshot)


# ---- worker-vs-edit race (version-CAS) ----
def test_stale_version_on_concurrent_edit():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    # simulate: worker advanced the plan (version bumped) after we read it
    stale = repo.get("p1")  # version N
    advanced = repo.get("p1")
    advanced.bump_version()
    repo.save(advanced)  # now N+1
    # now try to save the stale copy
    stale.bump_version()  # also N+1, but based on N
    with pytest.raises(StaleVersionError):
        repo.save(stale)


# ---- control: resume from review ----
def test_resume_from_review_advances_to_executing():
    plan = _plan_with_goal()  # AWAITING_REVIEW
    uow, repo = uow_with(plan)
    resume_from_review("p1", uow)
    assert repo.get("p1").phase == PlanPhase.EXECUTING


def test_resume_when_not_awaiting_review_raises():
    plan = _plan_with_goal()
    plan.phase = PlanPhase.EXECUTING
    uow, _ = uow_with(plan)
    with pytest.raises(InvalidEditError):
        resume_from_review("p1", uow)
