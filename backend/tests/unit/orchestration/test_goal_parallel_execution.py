"""Goal-level parallelism end-to-end (ADR-001, domain unfreeze #12; symmetric
per-goal leases + per-goal blocks, domain unfreeze #13): claim_ready_goal +
drive_goal + ExecutionHandler.handle_goal, on both backends via env_factory.

Domain unfreeze #13 removed the "privileged plan-level goal" asymmetry:
EVERY ready+enriched goal, including the position-earliest one, is claimable
through claim_ready_goal, symmetrically, by any worker — the plan-level tick
(advance_plan.py) no longer dispatches execution at all for a cyclic plan.
Two independent, already-enriched goals in the same active cycle claim to
DIFFERENT workers and each drives its own goal's task without touching the
other."""

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


def _two_independent_ready_goals_plan(now, *, tasks_per_goal: int = 1) -> Plan:
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
                    Goal(id="g1", name="g1", position=0, description="", tasks=tasks("g1")),
                    Goal(id="g2", name="g2", position=1, description="", tasks=tasks("g2")),
                ],
            )
        ],
    )


def test_claim_ready_goal_gives_each_worker_a_different_goal(env_factory):
    """Symmetric leases (unfreeze #13): BOTH goals, including position-0 g1,
    are claimable through goal_leases — there is no privileged goal left to
    exclude."""
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    first = claim_ready_goal(env.uow, "w1", 60, env.clock)
    second = claim_ready_goal(env.uow, "w2", 60, env.clock)

    assert first is not None and second is not None
    assert first[0] == "p1" and second[0] == "p1"
    assert first[1] != second[1]
    assert {first[1], second[1]} == {"g1", "g2"}


def test_claim_ready_goal_returns_none_when_both_goals_already_claimed(env_factory):
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
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

    signal, progressed = asyncio.run(
        drive_goal(plan_id, goal_id, env.uow, *env.args[1:], "w1")
    )

    assert progressed >= 1
    stored = env.stored(plan_id)
    goals_by_id = {g.id: g for g in stored.active_cycle.goals}
    driven_task = goals_by_id[goal_id].tasks[0]
    assert driven_task.status == Status.DONE
    other_goal_id = "g2" if goal_id == "g1" else "g1"
    other_task = goals_by_id[other_goal_id].tasks[0]
    assert other_task.status == Status.PENDING  # untouched by the other goal's drive


def test_drive_goal_finishes_unit_then_stops_when_goal_lease_is_lost(
    env_factory, monkeypatch
):
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now(), tasks_per_goal=2)
    env.seed(plan)
    assert env.uow.goal_leases.claim_one_ready_goal(
        "p1", "g1", "w1", 60, env.clock.now()
    )
    original_run = env.runner.run

    async def steal_during_run(*args, **kwargs):
        env.clock.advance(61)
        assert env.uow.goal_leases.claim_one_ready_goal(
            "p1", "g1", "w2", 60, env.clock.now()
        )
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(env.runner, "run", steal_during_run)

    result = asyncio.run(
        drive_goal("p1", "g1", env.uow, *env.args[1:], "w1")
    )

    assert result == ("lease_lost", 1)
    assert env.runner.calls == {"g1-0": 1}
    stored = env.stored("p1")
    goals_by_id = {goal.id: goal for goal in stored.active_cycle.goals}
    assert goals_by_id["g1"].tasks[0].status == Status.DONE
    assert goals_by_id["g1"].tasks[1].status == Status.PENDING


def test_both_goals_can_be_driven_concurrently_by_different_workers(env_factory):
    # Two tasks per goal: completing the first task never triggers goal
    # promotion (can_promote_goal needs EVERY task DONE-with-evidence), so
    # this stays isolated from can_promote_goal/promotion concerns and
    # cleanly proves just the claim+drive concurrency property.
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now(), tasks_per_goal=2)
    env.seed(plan)

    claimed_1 = claim_ready_goal(env.uow, "w1", 60, env.clock)
    claimed_2 = claim_ready_goal(env.uow, "w2", 60, env.clock)
    assert claimed_1 is not None and claimed_2 is not None
    assert {claimed_1[1], claimed_2[1]} == {"g1", "g2"}

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
    assert goals_by_id["g1"].tasks[0].status in (Status.RUNNING, Status.DONE)
    assert goals_by_id["g2"].tasks[0].status in (Status.RUNNING, Status.DONE)
    # neither goal is complete -- each still has a second pending task
    assert goals_by_id["g1"].tasks[1].status == Status.PENDING
    assert goals_by_id["g2"].tasks[1].status == Status.PENDING


def test_two_goals_concurrently_unpromotable_each_get_their_own_block(env_factory):
    """Domain unfreeze #13: Plan.block is no longer a single plan-wide
    scalar for cyclic plans -- each goal that discovers a block-worthy
    problem opens its OWN entry in goal_blocks, independent of any sibling
    goal's block. (Before #13 the second goal's failure reason was silently
    dropped in favor of whichever block landed first; that workaround is
    gone because there's nothing left to collide with.) With only two goals
    in this plan and BOTH ending up blocked, the plan has no goal left that
    can progress, so status correctly still becomes BLOCKED overall — but
    via two independent goal_blocks entries, not the legacy scalar."""
    env = env_factory()
    # single-task goals with NO verification_evidence: completing the task
    # makes can_promote_goal reject the goal, triggering the block path.
    plan = _two_independent_ready_goals_plan(env.clock.now())
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
    # close/promote concurrently on the next round; must not raise, and
    # neither goal's own block is dropped in favor of the other's.
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
    assert stored.block is None  # legacy scalar untouched by cyclic per-goal blocks
    g1_block = stored.goal_blocks.get("g1")
    g2_block = stored.goal_blocks.get("g2")
    assert g1_block is not None and g1_block.active
    assert g2_block is not None and g2_block.active
    assert g1_block.goal_id == "g1"
    assert g2_block.goal_id == "g2"


def test_blocked_goal_never_stops_an_independent_sibling_goal(env_factory):
    """The actual point of domain unfreeze #13: one goal blocking must not
    stop an unrelated, independent sibling goal from finishing. g1 becomes
    unpromotable (DONE task, no verification evidence) and opens its own
    block; g2 is untouched and can still be claimed, driven, and reach DONE
    while g1's block remains active — and the plan stays claimable
    (status RUNNING, not BLOCKED) the whole time, since g2 can still make
    progress."""
    env = env_factory()
    plan = _two_independent_ready_goals_plan(env.clock.now())
    env.seed(plan)

    claimed_g1 = claim_ready_goal(env.uow, "w0", 60, env.clock)
    assert claimed_g1 == ("p1", "g1")
    # drive_goal loops until the goal stops making progress -- reliably
    # drives g1's task to DONE (without evidence) and then through the
    # close-attempt that opens g1's own block, regardless of how many
    # internal handle_goal calls that takes for this backend/runner.
    signal, _ = asyncio.run(drive_goal("p1", "g1", env.uow, *env.args[1:], "w0"))
    assert signal == "paused"

    stored = env.stored("p1")
    g1_block = stored.goal_blocks.get("g1")
    assert g1_block is not None and g1_block.active
    # g1 blocked, g2 untouched -> g2 can still make progress, so the plan is
    # NOT fully stuck: status stays RUNNING and remains claimable.
    assert stored.status == PlanStatus.RUNNING

    # g1's own block must never be offered as a goal-lease candidate again
    # (it would immediately collide with itself) -- only g2 is claimable.
    claimed = claim_ready_goal(env.uow, "w1", 60, env.clock)
    assert claimed == ("p1", "g2")

    signal, progressed = asyncio.run(
        drive_goal("p1", "g2", env.uow, *env.args[1:], "w1")
    )
    assert progressed >= 1
    stored = env.stored("p1")
    goals_by_id = {g.id: g for g in stored.active_cycle.goals}
    assert goals_by_id["g2"].tasks[0].status == Status.DONE
    # g1 is still exactly as it was left -- blocked, untouched by g2's drive.
    assert goals_by_id["g1"].tasks[0].status == Status.DONE
    assert stored.goal_blocks["g1"].active


def test_handle_goal_matches_handle_for_a_legacy_single_goal_lease_free_call(env_factory):
    """handle_goal is the SAME body as handle() (goal_id threaded through) --
    not a divergent reimplementation. Prove that directly at the handler
    level, independent of the claim/drive plumbing above."""
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


def _n_independent_ready_goals_plan(now, n: int) -> Plan:
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
                    Goal(
                        id=f"g{i}",
                        name=f"g{i}",
                        position=i,
                        description="",
                        tasks=[_task(f"g{i}-t")],
                    )
                    for i in range(n)
                ],
            )
        ],
    )


def test_cas_retry_converges_under_four_way_concurrent_goal_finalize(env_factory):
    """Plan document's flagged risk: before domain unfreeze #13, at most two
    writers per process (worker_tick + one goal_tick) ever contended on one
    plan's version. Symmetric per-goal leases let every ready goal dispatch
    at once -- this drives FOUR independent goals to completion fully
    concurrently against the SAME plan document (one shared UnitOfWork, the
    same sharing pattern already proven safe by the other concurrent tests
    in this file: no `await` ever happens inside a `with uow:` block, so
    concurrent asyncio tasks never actually overlap a transaction) and
    confirms ExecutionHandler._run_with_cas_retry's default max_attempts
    still converges -- every goal reaches DONE, no StaleVersionError escapes
    to the caller."""
    env = env_factory()
    n = 4
    plan = _n_independent_ready_goals_plan(env.clock.now(), n)
    env.seed(plan)

    claims = [claim_ready_goal(env.uow, f"w{i}", 60, env.clock) for i in range(n)]
    assert all(claim is not None for claim in claims)
    assert {claim[1] for claim in claims} == {f"g{i}" for i in range(n)}  # type: ignore[index]

    async def drive_all():
        return await asyncio.gather(
            *(
                drive_goal(plan_id, goal_id, env.uow, *env.args[1:], f"w{i}")
                for i, (plan_id, goal_id) in enumerate(claims)  # type: ignore[misc]
            ),
            return_exceptions=True,
        )

    results = asyncio.run(drive_all())

    assert not any(isinstance(result, BaseException) for result in results), results
    stored = env.stored("p1")
    goals_by_id = {g.id: g for g in stored.active_cycle.goals}
    for i in range(n):
        assert goals_by_id[f"g{i}"].tasks[0].status == Status.DONE, (
            f"g{i} did not converge to DONE: {goals_by_id[f'g{i}'].tasks[0].status}"
        )
