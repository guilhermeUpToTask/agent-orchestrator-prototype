"""Worker-loop tests: the full claim->drive->release cycle, crash recovery via
lease expiry + re-claim, the driver-model claim predicate, and the regression
for the worker-tick spin (tick reports PROGRESS, not claiming)."""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import timedelta

from domain.policies.retry_policies import RetryPolicy
from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.entities.agent_spec import AgentSpec
from domain.value_objects.lifecycle import Status

from application.use_cases.control import finish_review
from application.use_cases.run_worker import drive_plan, worker_tick
from application.testing.fakes import (
    FakeClock,
    InMemoryPlanRepository,
    InMemoryOutbox,
    InMemoryUnitOfWork,
    InMemoryAgentRepository,
    NoOpWorkspace,
    CollectingEventSink,
    DummyAgentRunner,
)


def agent():
    return AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


def plan_with_chain():
    """Two goals where g2 depends on g1 — the classic case that produced
    pending-noise in the old system."""
    g1 = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[Task(id="g1t0", name="t", position=0, description="", agent_id="a1")],
    )
    g2 = Goal(
        id="g2",
        name="g2",
        position=1,
        description="",
        depends_on=["g1"],
        tasks=[Task(id="g2t0", name="t", position=0, description="", agent_id="a1")],
    )
    return Plan(id="p1", brief="b", phase=PlanPhase.RUNNING, goals=[g1, g2])


def harness(plan, script=None):
    clock = FakeClock()
    repo = InMemoryPlanRepository(clock)
    repo.add(plan)
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    runner = DummyAgentRunner(script or {})
    agents = InMemoryAgentRepository([agent()], default_id="a1")
    return (
        repo,
        uow,
        runner,
        agents,
        NoOpWorkspace(),
        CollectingEventSink(),
        clock,
    )


def test_worker_tick_drives_plan_to_review_gate():
    """Execution exhausts into REVIEW (the post-exec gate) — DONE only comes from
    the human finish."""
    repo, uow, runner, agents, ws, sink, clock = harness(plan_with_chain())
    did_work = asyncio.run(worker_tick(uow, runner, agents, ws, sink, clock, "w1"))
    assert did_work
    final = repo.get("p1")
    assert final.phase == PlanPhase.REVIEW
    assert all(t.status == Status.DONE for g in final.goals for t in g.tasks)
    finish_review("p1", uow)
    assert repo.get("p1").phase == PlanPhase.DONE


def test_worker_tick_returns_false_when_nothing_to_claim():
    repo = InMemoryPlanRepository()  # empty
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    did = asyncio.run(
        worker_tick(
            uow,
            DummyAgentRunner(),
            InMemoryAgentRepository([agent()], "a1"),
            NoOpWorkspace(),
            CollectingEventSink(),
            FakeClock(),
            "w1",
        )
    )
    assert did is False


def test_dependent_goal_runs_only_after_dependency_no_pending_noise():
    """The reconciler-killer at the loop level: g2 never executes before g1 is
    done, and there is no stuck/pending state to reconcile."""
    repo, uow, runner, agents, ws, sink, clock = harness(plan_with_chain())
    asyncio.run(drive_plan("p1", uow, runner, agents, ws, sink, clock, "w1"))
    # both ran, in order; g2's task only after g1 completed
    assert runner.calls.get("g1t0") == 1
    assert runner.calls.get("g2t0") == 1
    final = repo.get("p1")
    # g2 was never left "pending and stuck" — it's cleanly DONE
    assert final.goals[1].status == Status.DONE


def test_crash_recovery_via_lease_expiry_and_reclaim():
    """Worker w1 CLAIMS the plan, completes one task, then 'dies' without
    releasing. While w1's lease is live the plan is not claimable; once the lease
    expires, w2 reclaims and finishes purely from persisted state — no
    reconciler, just lease + re-claim."""
    repo, uow, runner1, agents, ws, sink, clock = harness(plan_with_chain())

    async def w1_claims_then_dies():
        from application.use_cases.advance_plan import advance_plan

        claimed = uow.plans.claim_one_unit("w1", lease_seconds=60)
        assert claimed is not None and claimed.id == "p1"
        await advance_plan("p1", uow, runner1, agents, ws, sink, clock)
        # w1 crashes here: no release, lease left dangling

    asyncio.run(w1_claims_then_dies())
    mid = repo.get("p1")
    assert mid.phase == PlanPhase.RUNNING
    assert mid.goals[0].tasks[0].status == Status.DONE

    # w1's lease is still live -> the plan is invisible to other workers
    runner2 = DummyAgentRunner()
    did = asyncio.run(worker_tick(uow, runner2, agents, ws, sink, clock, "w2"))
    assert did is False and runner2.calls == {}

    # lease expires -> w2 reclaims and resumes from persisted state
    clock.advance(61)
    asyncio.run(worker_tick(uow, runner2, agents, ws, sink, clock, "w2"))
    final = repo.get("p1")
    assert final.phase == PlanPhase.REVIEW  # execution exhausted -> post-exec gate
    # w2 only ran the REMAINING task (g2t0), not the already-done one (g1t0)
    assert "g1t0" not in runner2.calls
    assert runner2.calls.get("g2t0") == 1


def test_claim_predicate_is_the_driver_model():
    """Only ARCHITECTURE / ENRICHING / RUNNING are worker-claimable. Conversational
    phases (DISCOVERY, REPLANNING) and the gates (AWAITING_REVIEW, REVIEW) are
    invisible to workers — what isn't ready is never selected, so it never churns."""
    clock = FakeClock()
    repo = InMemoryPlanRepository(clock)
    unclaimable = [
        PlanPhase.DISCOVERY,
        PlanPhase.REPLANNING,
        PlanPhase.AWAITING_REVIEW,
        PlanPhase.REVIEW,
        PlanPhase.DONE,
        PlanPhase.FAILED,
    ]
    for i, phase in enumerate(unclaimable):
        repo.add(Plan(id=f"u{i}", brief="b", phase=phase))
    assert repo.claim_one_unit("w1", 60) is None

    for i, phase in enumerate(
        [PlanPhase.ARCHITECTURE, PlanPhase.ENRICHING, PlanPhase.RUNNING]
    ):
        repo.add(Plan(id=f"c{i}", brief="b", phase=phase))
    claimed_ids = {repo.claim_one_unit("w1", 60).id for _ in range(3)}
    assert claimed_ids == {"c0", "c1", "c2"}
    assert repo.claim_one_unit("w1", 60) is None  # all leases live now


def test_worker_tick_reports_progress_not_claiming():
    """Regression for the hot claim->release spin: a claimable plan whose only
    work is backing off must yield tick == False (caller sleeps), even though a
    claim technically succeeded."""
    plan = plan_with_chain()
    repo_clock = FakeClock()
    repo = InMemoryPlanRepository(repo_clock)
    # gate the first task into the future -> scan says NOT_READY immediately
    plan.goals[0].tasks[0].retry_not_before = repo_clock.now() + timedelta(seconds=300)
    repo.add(plan)
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    runner = DummyAgentRunner()
    agents = InMemoryAgentRepository([agent()], "a1")

    did = asyncio.run(
        worker_tick(
            uow, runner, agents, NoOpWorkspace(), CollectingEventSink(), repo_clock, "w1"
        )
    )
    assert did is False  # claimed, but zero progress -> sleep, don't spin
    assert runner.calls == {}  # nothing executed

    # once the gate expires the same tick DOES report progress
    repo_clock.advance(301)
    did = asyncio.run(
        worker_tick(
            uow, runner, agents, NoOpWorkspace(), CollectingEventSink(), repo_clock, "w1"
        )
    )
    assert did is True
    assert runner.calls.get("g1t0") == 1
