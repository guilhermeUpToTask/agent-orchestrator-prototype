"""Goal-level parallelism end-to-end (ADR-001, domain unfreeze #12 / Phase 3c):
claim_ready_goal + drive_goal + ExecutionHandler.handle_goal, on both backends
via env_factory.

IMPORTANT invariant, found via a LIVE walkthrough with two real worker
processes (not caught by any earlier unit test, since none exercised two
genuinely concurrent OS-level workers against the plan-level lease AND the
goal-level lease at once): claim_ready_goal must NEVER offer the plan's
earliest non-terminal goal (by position) as a goal-lease candidate, because
that is exactly the goal `advance_plan.py`'s plan-level tick already drives
via `next_action` -- regardless of that goal's own readiness. Before this
fix, a goal-lease-holding worker and a plan-lease-holding worker could both
independently dispatch a REAL agent run for the identical task; observed
live, this discarded a genuinely successful 268-second run as stale once a
racing attempt from the other lease moved the task's identity on."""

from __future__ import annotations

import asyncio

from src.app.handlers.execution_handler import ExecutionHandler
from src.app.use_cases.claim_ready_goal import claim_ready_goal
from src.app.use_cases.run_worker import drive_goal
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status


def _task(task_id: str) -> Task:
    return Task(id=task_id, name=task_id, position=0, description="", agent_id="a1")


def _independent_ready_goals_plan(now, goal_ids: list[str], *, tasks_per_goal: int = 1) -> Plan:
    """`goal_ids` in position order (position 0 is the "plan-level" goal --
    the one claim_ready_goal must always exclude). None have `depends_on`
    each other, so all are independently ready."""

    def tasks(prefix: str) -> list[Task]:
        return [_task(f"{prefix}-{i}") for i in range(tasks_per_goal)]

    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                status=CycleStatus.ACTIVE,
                started_at=now,
                goals=[
                    Goal(id=gid, name=gid, position=i, description="", tasks=tasks(gid))
                    for i, gid in enumerate(goal_ids)
                ],
            )
        ],
    )


def _two_independent_ready_goals_plan(now, *, tasks_per_goal: int = 1) -> Plan:
    return _independent_ready_goals_plan(now, ["g1", "g2"], tasks_per_goal=tasks_per_goal)


def _three_independent_ready_goals_plan(now, *, tasks_per_goal: int = 1) -> Plan:
    return _independent_ready_goals_plan(now, ["g1", "g2", "g3"], tasks_per_goal=tasks_per_goal)


def test_claim_ready_goal_never_returns_the_plan_level_earliest_goal(env_factory):
    """The exact bug found live: with only two independent ready goals, the
    earliest (g1, position 0) is what the plan-level tick already owns --
    claim_ready_goal must always skip it and return the other one, never
    both, no matter how many times it's called."""
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    first = claim_ready_goal(env.uow, "w1", 60, env.clock)
    assert first == ("p1", "g2")

    second = claim_ready_goal(env.uow, "w2", 60, env.clock)
    assert second is None  # g1 is never offered; g2 is already claimed


def test_claim_ready_goal_gives_each_worker_a_different_non_plan_level_goal(env_factory):
    env = env_factory()
    plan = _three_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    first = claim_ready_goal(env.uow, "w1", 60, env.clock)
    second = claim_ready_goal(env.uow, "w2", 60, env.clock)

    assert first is not None and second is not None
    assert first[0] == "p1" and second[0] == "p1"
    assert first[1] != second[1]
    assert {first[1], second[1]} == {"g2", "g3"}  # g1 (plan-level) never offered


def test_claim_ready_goal_returns_none_when_both_non_plan_level_goals_claimed(env_factory):
    env = env_factory()
    plan = _three_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    claim_ready_goal(env.uow, "w1", 60, env.clock)
    claim_ready_goal(env.uow, "w2", 60, env.clock)

    assert claim_ready_goal(env.uow, "w3", 60, env.clock) is None


def test_drive_goal_progresses_only_its_own_goal(env_factory):
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    claimed = claim_ready_goal(env.uow, "w1", 60, env.clock)
    assert claimed is not None
    plan_id, goal_id = claimed
    assert goal_id == "g2"  # g1 is the plan-level goal, never claimable here

    signal, progressed = asyncio.run(
        drive_goal(plan_id, goal_id, env.uow, *env.args[1:], "w1")
    )

    assert progressed >= 1
    stored = env.stored(plan_id)
    goals_by_id = {g.id: g for g in stored.active_cycle.goals}
    assert goals_by_id["g2"].tasks[0].status == Status.DONE
    assert goals_by_id["g1"].tasks[0].status == Status.PENDING  # untouched


def test_two_non_plan_level_goals_can_be_driven_concurrently_by_different_workers(env_factory):
    # Two tasks per goal: completing the first task never triggers goal
    # promotion (can_promote_goal needs EVERY task DONE-with-evidence), so
    # this stays isolated from the separate, already-known "Plan.block is a
    # single plan-wide scalar" limitation (goal completion/promotion-failure
    # handling under concurrent goals is explicitly out of scope for this
    # phase) and cleanly proves just the claim+drive concurrency property.
    env = env_factory()
    plan = _three_independent_ready_goals_plan(env.clock.now(), tasks_per_goal=2)
    env.seed(plan)

    claimed_1 = claim_ready_goal(env.uow, "w1", 60, env.clock)
    claimed_2 = claim_ready_goal(env.uow, "w2", 60, env.clock)
    assert claimed_1 is not None and claimed_2 is not None
    assert {claimed_1[1], claimed_2[1]} == {"g2", "g3"}

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    async def drive_both():
        plan_now = env.stored("p1")
        return await asyncio.gather(
            execution.handle_goal(claimed_1[0], claimed_1[1], plan_now, env.uow),
            execution.handle_goal(claimed_2[0], claimed_2[1], plan_now, env.uow),
        )

    asyncio.run(drive_both())

    stored = env.stored("p1")
    goals_by_id = {g.id: g for g in stored.active_cycle.goals}
    assert goals_by_id["g2"].tasks[0].status in (Status.RUNNING, Status.DONE)
    assert goals_by_id["g3"].tasks[0].status in (Status.RUNNING, Status.DONE)
    # neither claimed goal is complete -- each still has a second pending task
    assert goals_by_id["g2"].tasks[1].status == Status.PENDING
    assert goals_by_id["g3"].tasks[1].status == Status.PENDING
    # g1 (plan-level, never claimed here) is fully untouched
    assert goals_by_id["g1"].tasks[0].status == Status.PENDING


def test_two_non_plan_level_goals_concurrently_unpromotable_blocks_once_not_crashes(env_factory):
    """Plan.block is still a single plan-wide scalar (deliberately not
    reshaped per-goal this phase -- BLOCKED is a whole-plan status by
    design). If goal-level parallelism lets two DIFFERENT goals discover a
    block-worthy problem in the same tick, the second must degrade to a
    graceful PAUSED, never an uncaught InvalidEditError from open_block's
    "already active" guard."""
    env = env_factory()
    # single-task goals with NO verification_evidence: completing the task
    # makes can_promote_goal reject the goal, triggering the block path.
    plan = _three_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    claimed_1 = claim_ready_goal(env.uow, "w1", 60, env.clock)
    claimed_2 = claim_ready_goal(env.uow, "w2", 60, env.clock)
    assert claimed_1 is not None and claimed_2 is not None

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    # drive each goal's single task to DONE first (still isolated calls).
    plan_now = env.stored("p1")
    asyncio.run(execution.handle_goal(claimed_1[0], claimed_1[1], plan_now, env.uow))
    plan_now = env.stored("p1")
    asyncio.run(execution.handle_goal(claimed_2[0], claimed_2[1], plan_now, env.uow))

    # both goals' tasks are now DONE-without-evidence -> both attempt to
    # close/promote concurrently on the next round; must not raise.
    plan_now = env.stored("p1")

    async def drive_both_to_block():
        return await asyncio.gather(
            execution.handle_goal(claimed_1[0], claimed_1[1], plan_now, env.uow),
            execution.handle_goal(claimed_2[0], claimed_2[1], plan_now, env.uow),
            return_exceptions=True,
        )

    signals = asyncio.run(drive_both_to_block())

    assert not any(isinstance(result, BaseException) for result in signals), signals
    stored = env.stored("p1")
    assert stored.status.value == "blocked"
    assert stored.block is not None and stored.block.active


def test_handle_goal_matches_handle_for_a_legacy_single_goal_lease_free_call(env_factory):
    """handle_goal is the SAME body as handle() (goal_id threaded through) --
    not a divergent reimplementation. Prove that directly at the handler
    level, independent of the claim/drive plumbing above. Calls handle_goal
    directly (bypassing claim_ready_goal entirely), so it's unaffected by
    the plan-level-goal exclusion fix -- it can target g1 directly here."""
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    stored = env.stored("p1")
    signal = asyncio.run(execution.handle_goal("p1", "g1", stored, env.uow))

    assert signal.value in ("continue", "paused")
    after = env.stored("p1")
    goals_by_id = {g.id: g for g in after.active_cycle.goals}
    assert goals_by_id["g1"].tasks[0].status in (Status.RUNNING, Status.DONE)
    assert goals_by_id["g2"].tasks[0].status == Status.PENDING
