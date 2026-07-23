"""Per-goal blocks (domain unfreeze #13 — goal-level parallelism v2): a block
on one goal must never stop an unrelated, independent sibling goal from
progressing, and the plan-wide `status` only becomes BLOCKED when EVERY
non-terminal goal is blocked or transitively depends on one that is."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleStatus,
    PlanBlock,
    PlanStatus,
)
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _task(task_id: str, status: Status = Status.PENDING) -> Task:
    t = Task(id=task_id, name=task_id, position=0, description="")
    t.status = status
    return t


def _goal(goal_id: str, position: int, tasks: list[Task], depends_on: list[str] | None = None) -> Goal:
    return Goal(
        id=goal_id,
        name=goal_id,
        position=position,
        description="",
        tasks=tasks,
        depends_on=depends_on or [],
    )


def _cyclic_plan(goals: list[Goal]) -> Plan:
    return Plan(
        id="p1",
        project_id="project-1",
        brief="b",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                status=CycleStatus.ACTIVE,
                started_at=NOW,
                goals=goals,
            )
        ],
    )


def _block(goal_id: str, task_id: str = "t", legal_resolutions: list[str] | None = None) -> PlanBlock:
    return PlanBlock(
        id=f"block-{goal_id}",
        kind="execution_failure",
        explanation="boom",
        stage="implementation",
        goal_id=goal_id,
        task_id=task_id,
        legal_resolutions=legal_resolutions or ["retry_stage", "edit_task", "start_replan"],
        created_at=NOW,
    )


def test_one_goal_blocked_leaves_plan_running_and_claimable():
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])

    plan.open_block(_block("g1"))

    assert plan.status == PlanStatus.RUNNING  # NOT BLOCKED -- g2 can still progress
    assert plan.block is None  # legacy scalar untouched
    assert "g1" in plan.goal_blocks
    assert plan.goal_blocks["g1"].active


def test_every_goal_blocked_flips_plan_to_blocked():
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])

    plan.open_block(_block("g1"))
    plan.open_block(_block("g2", task_id="t2"))

    assert plan.status == PlanStatus.BLOCKED  # no non-terminal goal can progress


def test_dependent_goal_counts_as_stuck_when_its_dependency_is_blocked():
    """g3 depends_on g1; g1 is blocked. g3 was never itself given a block, but
    it can never become ready without g1 resolving first, so it must count
    as stuck too -- g2 is the only thing keeping the plan from BLOCKED."""
    plan = _cyclic_plan(
        [
            _goal("g1", 0, [_task("t1")]),
            _goal("g2", 1, [_task("t2")]),
            _goal("g3", 2, [_task("t3")], depends_on=["g1"]),
        ]
    )

    plan.open_block(_block("g1"))
    assert plan.status == PlanStatus.RUNNING  # g2 still unblocked

    plan.open_block(_block("g2", task_id="t2"))
    assert plan.status == PlanStatus.BLOCKED  # g1 blocked, g2 blocked, g3 stuck behind g1


def test_resolving_any_active_block_restores_running():
    """Resolving a block makes THAT goal actionable again (retriable/edited/
    replanned) even before it's actually re-dispatched -- so the plan
    immediately has a viable path forward again and returns to RUNNING, even
    while a DIFFERENT goal's block is still active."""
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])
    plan.open_block(_block("g1"))
    plan.open_block(_block("g2", task_id="t2"))
    assert plan.status == PlanStatus.BLOCKED

    plan.resolve_block("start_replan", NOW, goal_id="g1")
    assert plan.status == PlanStatus.RUNNING  # g1 is actionable again
    assert plan.goal_blocks["g2"].active  # g2's block is untouched, still open

    # A block reopening on g1 (e.g. it fails again) with g2 still blocked
    # goes fully stuck once more.
    plan.open_block(_block("g1"))
    assert plan.status == PlanStatus.BLOCKED

    plan.resolve_block("start_replan", NOW, goal_id="g2")
    assert plan.status == PlanStatus.RUNNING  # g1 still blocked, but g2 now viable


def test_resolving_one_of_two_blocks_does_not_touch_the_other():
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])
    plan.open_block(_block("g1"))
    plan.open_block(_block("g2", task_id="t2"))

    plan.resolve_block("start_replan", NOW, goal_id="g1")

    assert not plan.goal_blocks["g1"].active
    assert plan.goal_blocks["g2"].active  # untouched by resolving g1's


def test_a_different_goals_block_never_collides_with_open_block_guard():
    """Before #13 this would have raised InvalidEditError('a plan block is
    already active') since Plan.block was a single scalar shared by every
    goal -- the whole point of the per-goal dict is that this now just
    works."""
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])

    plan.open_block(_block("g1"))
    plan.open_block(_block("g2", task_id="t2"))  # must not raise

    assert plan.goal_blocks["g1"].active
    assert plan.goal_blocks["g2"].active


def test_completing_the_last_non_terminal_goal_does_not_force_blocked():
    """Edge case caught during plan review: complete_goal calls
    _recompute_cyclic_status, and if that goal was the LAST non-terminal one,
    plan_can_progress would vacuously return False (no non-terminal goal
    exists at all) -- which must NOT be misread as "stuck." A finished cycle
    is a completely different case (handled by advance_plan's own "every
    goal terminal -> enter review" path), not a block."""
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1", status=Status.DONE)])])
    assert plan.status == PlanStatus.RUNNING

    plan.complete_goal("g1")

    assert plan.status == PlanStatus.RUNNING  # NOT forced to BLOCKED
    assert plan.execution_goals[0].status == Status.DONE


def test_reopening_the_same_goals_block_still_raises():
    """The guard IS still meaningful for a genuine same-goal double-open."""
    import pytest
    from src.domain.errors.planning_errors import InvalidEditError

    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")])])
    plan.open_block(_block("g1"))

    with pytest.raises(InvalidEditError):
        plan.open_block(_block("g1"))
