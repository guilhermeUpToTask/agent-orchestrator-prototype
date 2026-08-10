from __future__ import annotations

import pytest

from agent_orchestrator.app.use_cases.operator_commands import (
    rebind_goal_agents,
    retry_planning_stage,
)
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.capability import Capability
from agent_orchestrator.domain.errors.agent_errors import RoleUnsatisfiableError
from agent_orchestrator.domain.errors.planning_errors import InvalidEditError
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import Cycle, PlanBlock, PlanStatus
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
from agent_orchestrator.domain.value_objects.lifecycle import Status


def role_agent(agent_id: str, capabilities: list[str]) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role=agent_id,
        model_role="smart",
        instructions="",
        capabilities=[
            Capability(id=capability, name=capability, description="")
            for capability in capabilities
        ],
        default_retry=RetryPolicy(),
    )


def blocked_plan(now) -> Plan:
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.ENRICHING,
        status=PlanStatus.BLOCKED,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=[
                    Goal(
                        id="g1",
                        name="frozen goal",
                        position=0,
                        description="",
                        tasks=[
                            Task(
                                id="t0",
                                name="frozen task 0",
                                position=0,
                                description="",
                                required_capabilities=["backend"],
                            ),
                            Task(
                                id="t1",
                                name="frozen task 1",
                                position=1,
                                description="",
                                required_capabilities=["backend"],
                            ),
                        ],
                    )
                ],
                started_at=now,
            )
        ],
        block=PlanBlock(
            id="block-1",
            kind="agent_capability",
            explanation="missing role coverage",
            stage="goal_enrichment",
            goal_id="g1",
            legal_resolutions=["start_replan"],
            created_at=now,
        ),
    )


def test_retry_agent_binding_uses_live_registry_and_preserves_tasks(env_factory):
    env = env_factory(
        agents=[
            role_agent("tests", ["backend", "test_authoring"]),
            role_agent("impl", ["backend", "implementation"]),
        ],
        default_agent_id="impl",
    )
    env.seed(blocked_plan(env.clock.now()))

    retry_planning_stage("p1", env.uow, env.clock, env.agents)

    stored = env.stored("p1")
    assert stored.status == PlanStatus.RUNNING
    assert stored.phase == PlanPhase.RUNNING
    assert stored.block is not None and stored.block.resolution == "retry_stage"
    rebound = stored.active_cycle.goals[0].tasks
    assert [task.name for task in rebound] == ["frozen task 0", "frozen task 1"]
    assert [task.required_capabilities for task in rebound] == [["backend"], ["backend"]]
    for task in rebound:
        assert task.role_agent_ids == {
            "test_author": "tests",
            "implementer": "impl",
        }
        assert task.agent_id == "impl"
    assert env.outbox_types() == ["BlockResolved"]


def test_retry_agent_binding_is_atomic_while_registry_has_a_gap(env_factory):
    env = env_factory(
        agents=[role_agent("tests", ["backend", "test_authoring"])],
        default_agent_id="tests",
    )
    env.seed(blocked_plan(env.clock.now()))

    # A coded DomainError, not a bare ValueError: the API's single status table
    # maps it to 422 so `POST /retry-stage` names the missing capabilities
    # instead of returning an opaque 500.
    with pytest.raises(RoleUnsatisfiableError, match="implementer"):
        retry_planning_stage("p1", env.uow, env.clock, env.agents)

    stored = env.stored("p1")
    assert stored.status == PlanStatus.BLOCKED
    assert stored.block is not None and stored.block.active
    for task in stored.active_cycle.goals[0].tasks:
        assert task.role_agent_ids == {}
        assert task.agent_id is None
    assert env.outbox_types() == []


def declared(agent_id: str, run_role: str, capabilities: list[str]) -> AgentSpec:
    """An agent that DECLARES a run role, rather than a free-form label."""
    return role_agent(agent_id, capabilities).model_copy(update={"role": run_role})


def test_a_tdd_task_never_binds_both_stages_to_the_test_author(env_factory):
    """The P8.4 defect, at unit speed instead of an hour of real models.

    A TDD task declares BOTH `test_authoring` and `implementation` because it
    has both stages. Role resolution used to union that whole list with the
    role's own capability, so the IMPLEMENTER role demanded an agent that could
    also author tests — and the only agent that qualified was the one whose
    instructions forbid implementing. Both stages bound to it and the GREEN
    stage could never succeed.
    """
    env = env_factory(
        agents=[
            # Holds BOTH capabilities, and is the first in the registry — the
            # exact shape that used to win the implementer role outright.
            declared("test-agent", "test_author", ["backend", "test_authoring", "implementation"]),
            declared("dev-agent", "implementer", ["backend", "implementation"]),
        ],
        default_agent_id="dev-agent",
    )
    env.seed(blocked_plan(env.clock.now()))

    retry_planning_stage("p1", env.uow, env.clock, env.agents)

    for task in env.stored("p1").active_cycle.goals[0].tasks:
        assert task.role_agent_ids["test_author"] == "test-agent"
        assert task.role_agent_ids["implementer"] == "dev-agent", (
            "the implementer stage was handed an agent instructed not to implement"
        )
        assert task.agent_id == "dev-agent"


def running_plan_with_a_bound_goal(now):
    """A HEALTHY plan: work in flight, no block, bindings already resolved."""
    plan = blocked_plan(now)
    plan.block = None
    plan.goal_blocks = {}
    # Rebinding is only legal while paused, so an in-flight attempt has
    # already finalized and cannot be re-attributed to an agent it never used.
    plan.status = PlanStatus.PAUSED
    plan.paused = True
    for task in plan.active_cycle.goals[0].tasks:
        task.role_agent_ids = {"test_author": "old-tests", "implementer": "old-impl"}
        task.agent_id = "old-impl"
    return plan


def test_agents_can_be_rebound_on_a_healthy_plan_without_a_block(env_factory):
    """Re-point in-flight work at a different runtime WITHOUT destroying it.

    Before this existed the only rebinding path was `retry_agent_binding`, which
    hard-requires an active `agent_capability` block. Switching a working plan
    from one runtime to another therefore had no supported route, and the
    workaround — delete the plan and start over — throws away the approved
    intent, the frozen contracts and every piece of accepted evidence. That is
    a bad trade for a change of agent.
    """
    env = env_factory(
        agents=[
            declared("codex-test", "test_author", ["backend", "test_authoring"]),
            declared("codex-dev", "implementer", ["backend", "implementation"]),
        ],
        default_agent_id="codex-dev",
    )
    env.seed(running_plan_with_a_bound_goal(env.clock.now()))

    rebind_goal_agents("p1", "g1", env.uow, env.clock, env.agents)

    stored = env.stored("p1")
    assert stored.status == PlanStatus.PAUSED  # untouched
    assert stored.block is None
    for task in stored.active_cycle.goals[0].tasks:
        assert task.role_agent_ids == {
            "test_author": "codex-test",
            "implementer": "codex-dev",
        }
        assert task.agent_id == "codex-dev"


def test_rebinding_never_touches_a_finished_task(env_factory):
    """A DONE task's accepted evidence was produced BY a specific agent against
    a specific revision. Re-pointing it would silently attribute that evidence
    to an agent that never ran, so finished work is left exactly as it is."""
    env = env_factory(
        agents=[
            declared("codex-test", "test_author", ["backend", "test_authoring"]),
            declared("codex-dev", "implementer", ["backend", "implementation"]),
        ],
        default_agent_id="codex-dev",
    )
    plan = running_plan_with_a_bound_goal(env.clock.now())
    finished = plan.active_cycle.goals[0].tasks[0]
    finished.status = Status.DONE
    env.seed(plan)

    rebind_goal_agents("p1", "g1", env.uow, env.clock, env.agents)

    tasks = env.stored("p1").active_cycle.goals[0].tasks
    assert tasks[0].status == Status.DONE
    assert tasks[0].role_agent_ids == {"test_author": "old-tests", "implementer": "old-impl"}
    assert tasks[1].role_agent_ids["implementer"] == "codex-dev"


def test_rebinding_a_running_plan_is_refused(env_factory):
    """Only while paused. Swapping the binding under an executing attempt would
    let the run finalize against a binding that is no longer the one on disk,
    and the ledger would attribute it to an agent that never saw the task.
    Pause is graceful, so requiring it makes the change unambiguous rather than
    merely unlikely to race."""
    env = env_factory(
        agents=[
            declared("codex-test", "test_author", ["backend", "test_authoring"]),
            declared("codex-dev", "implementer", ["backend", "implementation"]),
        ],
        default_agent_id="codex-dev",
    )
    plan = running_plan_with_a_bound_goal(env.clock.now())
    plan.status = PlanStatus.RUNNING
    plan.paused = False
    env.seed(plan)

    with pytest.raises(InvalidEditError, match="paused"):
        rebind_goal_agents("p1", "g1", env.uow, env.clock, env.agents)

    # and nothing moved
    for task in env.stored("p1").active_cycle.goals[0].tasks:
        assert task.role_agent_ids == {"test_author": "old-tests", "implementer": "old-impl"}
