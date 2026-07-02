"""Aggregate orchestration, navigation, edits, binding, factories — the behaviors
that kill the reconciler and the FAILED-loop, plus error paths."""

import pytest


from src.domain.policies.retry_policies import RetryPolicy
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.services.capability_matching import match_agent
from datetime import datetime, timezone
from src.domain.services.navigation import next_action

from src.domain.services import edit_service as ed
from src.domain.factories.plan_factory import PlanFactory
from src.domain.errors.tasks_errors import (
    GoalNotFoundError,
    TaskNotFoundError,
    GoalAlreadyRunningError,
)
from src.domain.errors.planning_errors import (
    PlanAlreadyTerminalError,
    EmptyPlanError,
    InvalidEditError,
)
from src.domain.value_objects.lifecycle import FailureKind, Status
from src.domain.value_objects.tasks_vos import TaskResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def task(i, status=Status.PENDING):
    t = Task(id=f"t{i}", name=f"t{i}", position=i, description="")
    t.status = status
    return t


def goal(gid, pos, tasks, status=Status.PENDING, deps=None):
    return Goal(
        id=gid,
        name=gid,
        position=pos,
        description="",
        tasks=tasks,
        status=status,
        depends_on=deps or [],
    )


def exec_plan(goals):
    return Plan(id="p", brief="b", phase=PlanPhase.RUNNING, goals=goals)


# ===== NAVIGATION: ordering =====
def test_first_incomplete_task_in_order():
    g = goal("g1", 0, [task(0, Status.DONE), task(1, Status.DONE), task(2)])
    _, t = next_action([g], _NOW)
    assert t.id == "t2"


def test_advance_to_next_goal():
    g1 = goal("g1", 0, [task(0, Status.DONE)], status=Status.DONE)
    g2 = goal("g2", 1, [task(0)])
    go, t = next_action([g1, g2], _NOW)
    assert go.id == "g2" and t.id == "t0"


def test_all_done_returns_none():
    g = goal("g1", 0, [task(0, Status.DONE)], status=Status.DONE)
    assert next_action([g], _NOW) is None


def test_position_order_respected_regardless_of_list_order():
    # list out of order; scan must honor position
    g = goal("g1", 0, [task(2), task(0), task(1)])
    _, t = next_action([g], _NOW)
    assert t.id == "t0"


# ===== RECONCILER-KILLER: unready goals never selected =====
def test_unready_dependent_goal_never_selected():
    g1 = goal("g1", 0, [task(0)])
    g2 = goal("g2", 1, [task(0)], deps=["g1"])
    go, _ = next_action([g1, g2], _NOW)
    assert go.id == "g1"  # g2 invisible until g1 DONE


def test_dependent_goal_selectable_after_dependency_done():
    g1 = goal("g1", 0, [task(0, Status.DONE)], status=Status.DONE)
    g2 = goal("g2", 1, [task(0)], deps=["g1"])
    go, _ = next_action([g1, g2], _NOW)
    assert go.id == "g2"


def test_multiple_unready_goals_no_noise():
    # chain of 3, only first actionable; others must not surface
    g1 = goal("g1", 0, [task(0)])
    g2 = goal("g2", 1, [task(0)], deps=["g1"])
    g3 = goal("g3", 2, [task(0)], deps=["g2"])
    go, _ = next_action([g1, g2, g3], _NOW)
    assert go.id == "g1"


# ===== FAILED-LOOP FIX =====
def test_failed_task_signals_goal_failed_not_loop():
    g = goal("g1", 0, [task(0, Status.FAILED)])
    go, sig = next_action([g], _NOW)
    assert sig == "GOAL_FAILED"


def test_failed_then_pending_skips_failed():
    g = goal("g1", 0, [task(0, Status.FAILED), task(1)])
    _, t = next_action([g], _NOW)
    assert t.id == "t1"


def test_skipped_task_is_skipped():
    g = goal("g1", 0, [task(0, Status.SKIPPED), task(1)])
    _, t = next_action([g], _NOW)
    assert t.id == "t1"


# ===== IDEMPOTENCY =====
def test_task_with_result_is_returned_for_finalization_not_skipped():
    # A non-terminal task that already has a result (crash after run, before the
    # finalizing commit) must be RETURNED so advance_plan can finalize it — NOT
    # hidden from the scan (which would close the goal with a live task).
    g = goal("g1", 0, [task(0)])
    g.tasks[0].result = TaskResult.success("already")
    goal_out, second = next_action([g], _NOW)
    assert second is not None and second.id == "t0"  # returned, not skipped


def test_start_task_idempotent_when_result_exists():
    g = goal("g1", 0, [task(0)])
    g.tasks[0].result = TaskResult.success("done")
    p = exec_plan([g])
    p.start_task("g1", "t0")
    assert p.goals[0].tasks[0].status != Status.RUNNING


# ===== AGGREGATE error paths =====
def test_unknown_goal_raises():
    p = exec_plan([goal("g1", 0, [task(0)])])
    with pytest.raises(GoalNotFoundError):
        p.start_task("nope", "t0")


def test_unknown_task_raises():
    p = exec_plan([goal("g1", 0, [task(0)])])
    with pytest.raises(TaskNotFoundError):
        p.start_task("g1", "nope")


def test_mutation_on_terminal_plan_raises():
    p = exec_plan([goal("g1", 0, [task(0)])])
    p.fail_goal("g1")  # -> phase FAILED
    with pytest.raises(PlanAlreadyTerminalError):
        p.start_task("g1", "t0")


def test_fail_goal_halts_plan():
    p = exec_plan([goal("g1", 0, [task(0)])])
    p.fail_goal("g1")
    assert p.phase == PlanPhase.FAILED


# ===== FULL retry cycle through the aggregate =====
def test_retry_cycle_requeue_then_succeed():
    p = exec_plan([goal("g1", 0, [task(0)])])
    p.start_task("g1", "t0")  # attempts 1
    p.fail_task("g1", "t0", "transient")
    assert p.retry_policy.should_retry(1, FailureKind.CONNECTION_ERROR)  # domain decides retry
    p.requeue_task("g1", "t0")
    p.start_task("g1", "t0")  # attempts 2
    p.complete_task("g1", "t0", TaskResult.success("ok"))
    assert (
        p.goals[0].tasks[0].status == Status.DONE and p.goals[0].tasks[0].attempt == 2
    )


def test_retry_exhaustion_becomes_terminal():
    p = exec_plan([goal("g1", 0, [task(0)])])
    rp = p.retry_policy
    p.start_task("g1", "t0")
    p.start_task("g1", "t0")
    p.start_task("g1", "t0")  # 3 attempts
    p.fail_task("g1", "t0", "transient")
    assert rp.should_retry(3, FailureKind.CONNECTION_ERROR) is False  # exhausted -> stays FAILED


# ===== EDIT service =====
def test_edit_add_and_renumber():
    g = goal("g1", 0, [task(0), task(1)])
    ed.add_task([g], "g1", Task(id="tX", name="tX", position=99, description=""))
    positions = sorted((t.id, t.position) for t in g.tasks)
    assert ("tX", 2) in positions


def test_edit_blocked_on_running_goal():
    g = goal("g1", 0, [task(0)], status=Status.RUNNING)
    with pytest.raises(GoalAlreadyRunningError):
        ed.remove_task([g], "g1", "t0")


def test_edit_unknown_goal_raises():
    with pytest.raises(GoalNotFoundError):
        ed.add_task([], "ghost", Task(id="t", name="t", position=0, description=""))


def test_reorder_must_list_exact_ids():
    g = goal("g1", 0, [task(0), task(1)])
    with pytest.raises(InvalidEditError):
        ed.reorder_tasks([g], "g1", ["t0"])  # missing t1


# ===== CAPABILITY MATCHING + BINDING =====
def test_match_and_default_fallback():
    backend = Capability(id="backend", name="b", description="")
    a = AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        capabilities=[backend],
        default_retry=RetryPolicy(),
    )
    assert match_agent(["backend"], [a], "def") == ("a1", False)
    assert match_agent(["db"], [a], "def") == ("def", True)


def test_bind_agents_records_fallbacks():
    backend = Capability(id="backend", name="b", description="")
    a = AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        capabilities=[backend],
        default_retry=RetryPolicy(),
    )
    g = goal(
        "g1",
        0,
        [
            Task(
                id="t0",
                name="t0",
                position=0,
                description="",
                required_capabilities=["backend"],
            ),
            Task(
                id="t1",
                name="t1",
                position=1,
                description="",
                required_capabilities=["unknown"],
            ),
        ],
    )
    p = Plan(id="p", brief="b", goals=[g])
    fell_back = p.bind_agents([a], "default")
    assert p.goals[0].tasks[0].agent_id == "a1"
    assert p.goals[0].tasks[1].agent_id == "default"
    assert fell_back == ["t1"]


# ===== FACTORIES =====
def test_factory_create_and_birth_invariant():
    p = PlanFactory.create("build x")
    assert p.phase == PlanPhase.DISCOVERY and p.version == 0 and p.brief == "build x"
    assert p.iteration == 1
    with pytest.raises(EmptyPlanError):
        PlanFactory.create("  ")


def test_factory_reconstruct_roundtrip():
    p = PlanFactory.create("build x")
    g = goal("g1", 0, [task(0)])
    p.goals.append(g)
    restored = PlanFactory.reconstruct(p.model_dump())
    assert restored.id == p.id and restored.goals[0].id == "g1"
    # reconstruct must NOT regenerate identity or reset state
    assert restored.version == p.version
