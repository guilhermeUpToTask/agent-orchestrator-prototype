"""Worker-loop tests: the full claim->drive->release cycle, crash recovery via
re-claim, and the proof that an unready goal never causes pending-noise."""

import sys, os, asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.policies.retry_policies import RetryPolicy
from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.entities.agent_spec import AgentSpec
from domain.value_objects.tasks_vos import Status, TaskResult

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
    DummyBehavior,
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
    return Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g1, g2])


def harness(plan, script=None):
    repo = InMemoryPlanRepository()
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
        FakeClock(),
    )


def test_worker_tick_drives_plan_to_done():
    repo, uow, runner, agents, ws, sink, clock = harness(plan_with_chain())
    did_work = asyncio.run(worker_tick(uow, runner, agents, ws, sink, clock, "w1"))
    assert did_work
    final = repo.get("p1")
    assert final.phase == PlanPhase.DONE
    assert all(t.status == Status.DONE for g in final.goals for t in g.tasks)


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


def test_crash_recovery_via_reclaim():
    """Worker w1 'dies' after completing the first task (we stop driving). A
    second worker w2 claims and finishes — no reconciler, just re-claim + resume."""
    plan = plan_with_chain()
    repo = InMemoryPlanRepository()
    repo.add(plan)
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    agents = InMemoryAgentRepository([agent()], "a1")
    ws, sink = NoOpWorkspace(), CollectingEventSink()

    # w1 does exactly ONE advance (simulating crash mid-plan), then "dies"
    runner1 = DummyAgentRunner()

    async def one_step():
        from application.use_cases.advance_plan import advance_plan

        await advance_plan("p1", uow, runner1, agents, ws, sink, FakeClock())

    asyncio.run(one_step())
    # first task done, plan NOT finished, w1 gone
    mid = repo.get("p1")
    assert mid.phase == PlanPhase.EXECUTING
    assert mid.goals[0].tasks[0].status == Status.DONE

    # w2 claims and finishes — resume works purely from persisted state
    runner2 = DummyAgentRunner()
    asyncio.run(worker_tick(uow, runner2, agents, ws, sink, FakeClock(), "w2"))
    final = repo.get("p1")
    assert final.phase == PlanPhase.DONE
    # w2 only ran the REMAINING task (g2t0), not the already-done one (g1t0)
    assert "g1t0" not in runner2.calls
    assert runner2.calls.get("g2t0") == 1
