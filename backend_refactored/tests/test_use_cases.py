"""Tests for create_plan / apply_edit / control use cases."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.agent_spec import AgentSpec
from domain.entities.capability import Capability
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.errors.agent_errors import AgentNotFoundError, UnknownCapabilityError
from domain.errors.planning_errors import EmptyPlanError, InvalidEditError
from domain.errors.tasks_errors import (
    GoalAlreadyRunningError,
    InvalidTransitionError,
    StaleVersionError,
)
from domain.policies.retry_policies import RetryPolicy
from domain.value_objects.lifecycle import Status

from application.use_cases.create_plan import create_plan
from application.use_cases.apply_edit import (
    apply_edit,
    AddTask,
    RemoveTask,
    ReorderTasks,
    EditTaskRequirements,
    RebindTaskAgent,
)
from application.use_cases.control import finish_review, resume_from_review
from application.testing.fakes import (
    InMemoryAgentRepository,
    InMemoryCapabilityRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)


def uow_with(plan=None):
    repo = InMemoryPlanRepository()
    if plan:
        repo.add(plan)
    return InMemoryUnitOfWork(repo, InMemoryOutbox()), repo


CAP_NEW = Capability(id="new-cap", name="new capability", description="")


def make_agent(agent_id="a1", capabilities=()):
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role="agent",
        model_role="agent",
        instructions="",
        capabilities=list(capabilities),
        default_retry=RetryPolicy(),
    )


def catalogs(agents=None, capabilities=None, default_id="a-default"):
    """The reference repos apply_edit validates against."""
    agent_list = agents if agents is not None else [make_agent("a-default")]
    return (
        InMemoryCapabilityRepository(capabilities or [CAP_NEW]),
        InMemoryAgentRepository(agent_list, default_id=default_id),
    )


def edit(plan_id, e, uow, agents=None, capabilities=None):
    caps_repo, agents_repo = catalogs(agents, capabilities)
    apply_edit(plan_id, e, uow, caps_repo, agents_repo)


# ---- create_plan ----
def test_create_plan_returns_id():
    uow, repo = uow_with()
    pid = create_plan("build a REST API", "req-1", uow)
    assert pid and repo.get(pid).brief == "build a REST API"


def test_create_plan_starts_in_discovery_iteration_1():
    uow, repo = uow_with()
    pid = create_plan("build x", "req-1", uow)
    created = repo.get(pid)
    assert created.phase == PlanPhase.DISCOVERY
    assert created.iteration == 1


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
    edit("p1", AddTask("g1", Task(id="tX", name="tX", position=9, description="")), uow)
    saved = repo.get("p1")
    assert any(t.id == "tX" for t in saved.goals[0].tasks)
    assert saved.version == plan.version + 1


def test_apply_edit_remove_task():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    edit("p1", RemoveTask("g1", "t0"), uow)
    assert all(t.id != "t0" for t in repo.get("p1").goals[0].tasks)


def test_apply_edit_reorder():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    edit("p1", ReorderTasks("g1", ["t1", "t0"]), uow)
    saved = sorted(repo.get("p1").goals[0].tasks, key=lambda t: t.position)
    assert [t.id for t in saved] == ["t1", "t0"]


def test_apply_edit_on_running_goal_rejected():
    plan = _plan_with_goal(status=Status.RUNNING)
    uow, _ = uow_with(plan)
    with pytest.raises(GoalAlreadyRunningError):
        edit("p1", RemoveTask("g1", "t0"), uow)


def test_apply_edit_requirements_rematches_agent():
    """Locked rebind-on-edit rule: editing required_capabilities RE-RUNS
    match_agent — the binding follows the new requirements."""
    plan = _plan_with_goal()
    plan.goals[0].tasks[0].agent_id = "bound-agent"  # previously bound
    uow, repo = uow_with(plan)
    specialist = make_agent("a-specialist", capabilities=[CAP_NEW])
    edit(
        "p1",
        EditTaskRequirements("g1", "t0", ["new-cap"]),
        uow,
        agents=[make_agent("a-default"), specialist],
    )
    saved = repo.get("p1").goals[0].tasks[0]
    assert saved.required_capabilities == ["new-cap"]
    assert saved.agent_id == "a-specialist"  # rebound to the new best match


def test_apply_edit_requirements_unknown_capability_rejected():
    """Capability ids are validated at the edit boundary (DESIGN_NOTES #5): a bad
    id fails loudly instead of silently falling back to the default agent."""
    plan = _plan_with_goal()
    uow, _ = uow_with(plan)
    with pytest.raises(UnknownCapabilityError):
        edit("p1", EditTaskRequirements("g1", "t0", ["ghost-cap"]), uow)


def test_rebind_task_agent_is_explicit_override_no_rematch():
    plan = _plan_with_goal()
    uow, repo = uow_with(plan)
    override = make_agent("a-override")  # has NO capabilities at all
    edit(
        "p1",
        RebindTaskAgent("g1", "t0", "a-override"),
        uow,
        agents=[make_agent("a-default"), override],
    )
    assert repo.get("p1").goals[0].tasks[0].agent_id == "a-override"


def test_rebind_task_agent_requires_existing_agent():
    plan = _plan_with_goal()
    uow, _ = uow_with(plan)
    with pytest.raises(AgentNotFoundError):
        edit("p1", RebindTaskAgent("g1", "t0", "ghost"), uow)


def test_rebind_task_agent_rejected_unless_pending():
    plan = _plan_with_goal()
    plan.goals[0].status = Status.RUNNING
    plan.goals[0].tasks[0].status = Status.RUNNING
    uow, _ = uow_with(plan)
    with pytest.raises(InvalidEditError):
        edit("p1", RebindTaskAgent("g1", "t0", "a-default"), uow)


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


# ---- control: the two gates ----
def test_resume_from_review_advances_to_running():
    plan = _plan_with_goal()  # AWAITING_REVIEW
    uow, repo = uow_with(plan)
    resume_from_review("p1", uow)
    assert repo.get("p1").phase == PlanPhase.RUNNING


def test_resume_when_not_awaiting_review_raises():
    plan = _plan_with_goal()
    plan.phase = PlanPhase.RUNNING
    uow, _ = uow_with(plan)
    with pytest.raises(InvalidTransitionError):
        resume_from_review("p1", uow)


def test_finish_review_only_from_review_gate():
    plan = _plan_with_goal()
    plan.phase = PlanPhase.RUNNING
    uow, _ = uow_with(plan)
    with pytest.raises(InvalidTransitionError):
        finish_review("p1", uow)
