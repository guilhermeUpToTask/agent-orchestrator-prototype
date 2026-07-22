"""Worker-loop tests: the full claim->drive->release cycle, crash recovery via
lease expiry + re-claim, the driver-model claim predicate, and the regression
for the worker-tick spin. Parametrized over the in-memory fakes AND the real
SQLite lease (claim_one_unit/heartbeat/release on actual rows) — the crash
tests on sqlite are the truth-test for lease-based recovery."""

import asyncio

from datetime import timedelta

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status

from src.app.use_cases.advance_plan import advance_plan
from src.app.use_cases.control import finish_review
from src.app.use_cases.run_worker import _advance_with_heartbeats, drive_plan, worker_tick


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
    return Plan(project_id="project-1", id="p1", brief="b", phase=PlanPhase.RUNNING, goals=[g1, g2])


def test_worker_tick_drives_plan_to_review_gate(env_factory):
    """Execution exhausts into REVIEW (the post-exec gate) — DONE only comes from
    the human finish."""
    env = env_factory()
    env.seed(plan_with_chain())
    did_work = asyncio.run(worker_tick(*env.args, "w1"))
    assert did_work
    final = env.stored("p1")
    assert final.phase == PlanPhase.REVIEW
    assert all(t.status == Status.DONE for g in final.goals for t in g.tasks)
    finish_review("p1", env.uow)
    assert env.stored("p1").phase == PlanPhase.DONE


def test_worker_tick_returns_false_when_nothing_to_claim(env_factory):
    env = env_factory()  # empty store
    did = asyncio.run(worker_tick(*env.args, "w1"))
    assert did is False


def test_dependent_goal_runs_only_after_dependency_no_pending_noise(env_factory):
    """The reconciler-killer at the loop level: g2 never executes before g1 is
    done, and there is no stuck/pending state to reconcile."""
    env = env_factory()
    env.seed(plan_with_chain())
    asyncio.run(drive_plan("p1", *env.args, "w1"))
    # both ran, in order; g2's task only after g1 completed
    assert env.runner.calls.get("g1t0") == 1
    assert env.runner.calls.get("g2t0") == 1
    # g2 was never left "pending and stuck" — it's cleanly DONE
    assert env.stored("p1").goals[1].status == Status.DONE


def test_crash_recovery_via_lease_expiry_and_reclaim(env_factory):
    """Worker w1 CLAIMS the plan, completes one task, then 'dies' without
    releasing. While w1's lease is live the plan is not claimable; once the lease
    expires, w2 reclaims and finishes purely from persisted state — no
    reconciler, just lease + re-claim."""
    env = env_factory()
    env.seed(plan_with_chain())

    async def w1_claims_then_dies():
        claimed = env.uow.plans.claim_one_unit("w1", lease_seconds=60)
        assert claimed is not None and claimed.id == "p1"
        await advance_plan("p1", *env.args)
        # w1 crashes here: no release, lease left dangling

    asyncio.run(w1_claims_then_dies())
    mid = env.stored("p1")
    assert mid.phase == PlanPhase.RUNNING
    assert mid.goals[0].tasks[0].status == Status.DONE

    # w1's lease is still live -> the plan is invisible to other workers
    did = asyncio.run(worker_tick(*env.args, "w2"))
    assert did is False and env.runner.calls.get("g2t0") is None

    # lease expires -> w2 reclaims and resumes from persisted state
    env.clock.advance(61)
    asyncio.run(worker_tick(*env.args, "w2"))
    final = env.stored("p1")
    assert final.phase == PlanPhase.REVIEW  # execution exhausted -> post-exec gate
    # w2 only ran the REMAINING task (g2t0) exactly once
    assert env.runner.calls.get("g1t0") == 1  # still just w1's run
    assert env.runner.calls.get("g2t0") == 1


def test_claim_predicate_is_the_driver_model(env_factory):
    """Only ARCHITECTURE / ENRICHING / RUNNING are worker-claimable. Conversational
    phases (DISCOVERY, REPLANNING) and the gates (AWAITING_REVIEW, REVIEW) are
    invisible to workers — what isn't ready is never selected, so it never churns."""
    env = env_factory()
    unclaimable = [
        PlanPhase.DISCOVERY,
        PlanPhase.REPLANNING,
        PlanPhase.AWAITING_REVIEW,
        PlanPhase.REVIEW,
        PlanPhase.DONE,
        PlanPhase.FAILED,
    ]
    for i, phase in enumerate(unclaimable):
        env.seed(Plan(project_id=f"project-u{i}", id=f"u{i}", brief="b", phase=phase))
    assert env.uow.plans.claim_one_unit("w1", 60) is None

    for i, phase in enumerate([PlanPhase.ARCHITECTURE, PlanPhase.ENRICHING, PlanPhase.RUNNING]):
        env.seed(Plan(project_id=f"project-c{i}", id=f"c{i}", brief="b", phase=phase))
    claimed_ids = {env.uow.plans.claim_one_unit("w1", 60).id for _ in range(3)}
    assert claimed_ids == {"c0", "c1", "c2"}
    assert env.uow.plans.claim_one_unit("w1", 60) is None  # all leases live now


def test_release_frees_the_claim(env_factory):
    env = env_factory()
    env.seed(Plan(project_id="project-1", id="p1", brief="b", phase=PlanPhase.RUNNING))
    assert env.uow.plans.claim_one_unit("w1", 60).id == "p1"
    assert env.uow.plans.claim_one_unit("w2", 60) is None  # held by w1
    env.uow.plans.release("p1", "w2")  # someone else's release is a no-op
    assert env.uow.plans.claim_one_unit("w2", 60) is None
    env.uow.plans.release("p1", "w1")
    assert env.uow.plans.claim_one_unit("w2", 60).id == "p1"  # freed


def test_heartbeat_extends_only_own_lease(env_factory):
    env = env_factory()
    env.seed(Plan(project_id="project-1", id="p1", brief="b", phase=PlanPhase.RUNNING))
    assert env.uow.plans.claim_one_unit("w1", lease_seconds=60) is not None

    # w1 heartbeats at t+50 -> lease now runs to ~t+110
    env.clock.advance(50)
    env.uow.plans.heartbeat("p1", "w1")
    env.clock.advance(59)  # t+109: past the ORIGINAL expiry, inside the renewed one
    assert env.uow.plans.claim_one_unit("w2", 60) is None  # still held

    # a stranger's heartbeat must NOT extend the lease
    env.uow.plans.heartbeat("p1", "w2")
    env.clock.advance(2)  # t+111: renewed lease expired
    assert env.uow.plans.claim_one_unit("w2", 60) is not None


def test_worker_tick_reports_progress_not_claiming(env_factory):
    """Regression for the hot claim->release spin: a claimable plan whose only
    work is backing off must yield tick == False (caller sleeps), even though a
    claim technically succeeded."""
    env = env_factory()
    plan = plan_with_chain()
    # gate the first task into the future -> scan says NOT_READY immediately
    plan.goals[0].tasks[0].retry_not_before = env.clock.now() + timedelta(seconds=300)
    env.seed(plan)

    did = asyncio.run(worker_tick(*env.args, "w1"))
    assert did is False  # claimed, but zero progress -> sleep, don't spin
    assert env.runner.calls == {}  # nothing executed

    # once the gate expires the same tick DOES report progress
    env.clock.advance(301)
    did = asyncio.run(worker_tick(*env.args, "w1"))
    assert did is True
    assert env.runner.calls.get("g1t0") == 1


def test_heartbeat_failure_cancels_and_awaits_the_advance_task():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_advance() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def scenario() -> None:
        task = asyncio.create_task(pending_advance())
        await started.wait()

        def fail_heartbeat() -> None:
            raise RuntimeError("lease renewal failed")

        try:
            await _advance_with_heartbeats(
                0,
                fail_heartbeat,
                task,
            )
        except RuntimeError as exc:
            assert str(exc) == "lease renewal failed"
        else:
            raise AssertionError("heartbeat failure was not propagated")

        assert task.cancelled()
        assert cancelled.is_set()

    asyncio.run(scenario())
