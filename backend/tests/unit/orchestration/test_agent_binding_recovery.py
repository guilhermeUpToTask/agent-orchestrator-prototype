from __future__ import annotations

import pytest

from agent_orchestrator.app.use_cases.pause_resume import retry_planning_stage
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.capability import Capability
from agent_orchestrator.domain.errors.agent_errors import RoleUnsatisfiableError
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import Cycle, PlanBlock, PlanStatus
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy


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
