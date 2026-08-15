"""Operator disposal of a plan, on both backends via env_factory.

`delete_plan` is the supported way to start a genuinely fresh run: a project owns
one long-lived plan (ADR-003), so re-posting a brief reopens the SAME plan and
adds another cycle. Without this the only reset was deleting rows out of SQLite.

The interesting assertions are the ones the fake could get wrong and still look
right: that a live lease refuses the delete, and that nothing plan-scoped
survives it. Several plan-scoped tables carry no ON DELETE CASCADE, so on the
real backend a bare `DELETE FROM plans` either fails or orphans rows — running
these against SQLite is what makes "no leftovers" a fact rather than a claim.
"""

from __future__ import annotations

import pytest

from praxis_orchestrator.app.use_cases.delete_plan import delete_plan
from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.errors.planning_errors import PlanBusyError, PlanNotFoundError


def running_plan(plan_id: str = "p1") -> Plan:
    return Plan(
        project_id="project-1",
        id=plan_id,
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[
            Goal(
                id="g1",
                name="g1",
                position=0,
                description="",
                tasks=[Task(id="t0", name="t0", position=0, description="", agent_id="a1")],
            )
        ],
    )


def test_deletes_the_plan(env_factory):
    env = env_factory()
    env.seed(running_plan())

    delete_plan("p1", env.uow)

    with pytest.raises(PlanNotFoundError):
        with env.uow:
            env.uow.plans.get("p1")


def test_unknown_plan_is_not_found(env_factory):
    env = env_factory()
    with pytest.raises(PlanNotFoundError):
        delete_plan("nope", env.uow)


def test_refuses_while_a_worker_holds_a_live_lease(env_factory):
    """Deleting mid-action pulls the aggregate out from under a running agent:
    the finalize transaction re-reads the plan and would crash on a missing row
    AFTER the side effect already happened."""
    env = env_factory()
    env.seed(running_plan())
    claimed = env.uow.plans.claim_one_unit("worker-1", lease_seconds=300)
    assert claimed is not None and claimed.id == "p1"

    with pytest.raises(PlanBusyError):
        delete_plan("p1", env.uow)

    with env.uow:  # still there
        assert env.uow.plans.get("p1").id == "p1"


def test_deletable_once_the_lease_expires(env_factory):
    """A dead worker must not make a plan permanently undeletable — the same
    expiry that makes it reclaimable makes it disposable."""
    env = env_factory()
    env.seed(running_plan())
    env.uow.plans.claim_one_unit("worker-1", lease_seconds=300)

    env.clock.advance(seconds=301)
    delete_plan("p1", env.uow)

    with pytest.raises(PlanNotFoundError):
        with env.uow:
            env.uow.plans.get("p1")


def test_released_plan_is_deletable(env_factory):
    env = env_factory()
    env.seed(running_plan())
    env.uow.plans.claim_one_unit("worker-1", lease_seconds=300)
    env.uow.plans.release("p1", "worker-1")

    delete_plan("p1", env.uow)
    with pytest.raises(PlanNotFoundError):
        with env.uow:
            env.uow.plans.get("p1")


def test_request_binding_does_not_survive(env_factory):
    """`plan_requests` has a foreign key with no ON DELETE, so it must be removed
    explicitly. A surviving binding would make a replayed create return the id of
    a plan that no longer exists."""
    env = env_factory()
    env.seed(running_plan())
    with env.uow:
        env.uow.plans.bind_request_id("req-1", "p1")

    delete_plan("p1", env.uow)

    with env.uow:
        assert env.uow.plans.find_by_request_id("req-1") is None


def test_project_is_free_for_a_fresh_plan(env_factory):
    """The point of the command: after disposal the project owns no plan, so the
    next brief opens a new one instead of adding a cycle to the old one."""
    env = env_factory()
    env.seed(running_plan())
    with env.uow:
        assert env.uow.plans.find_by_project_id("project-1") == "p1"

    delete_plan("p1", env.uow)

    with env.uow:
        assert env.uow.plans.find_by_project_id("project-1") is None


def test_deleting_one_plan_leaves_another_alone(env_factory):
    env = env_factory()
    env.seed(running_plan("p1"))
    other = running_plan("p2")
    other.project_id = "project-2"
    env.seed(other)

    delete_plan("p1", env.uow)

    with env.uow:
        assert env.uow.plans.get("p2").id == "p2"
