"""Reasoner-failure handling in the worker-driven planning phases (un-freeze #2):
a transient reasoner failure arms the durable plan-level backoff gate and surfaces
a ReasonerFailed event instead of a silent worker.tick_failed loop; a permanent
failure (or an exhausted budget) fails the plan. On both backends via env_factory —
the claim predicate must honor the gate identically in fake and real SQLite.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.task import Task

from agent_orchestrator.app.handlers.base import Signal
from agent_orchestrator.app.handlers.planning_handler import PlanningHandler
from agent_orchestrator.app.ports import ReasonerUnavailable
from agent_orchestrator.app.testing.fakes import InMemoryCapabilityRepository


def goal(gid: str, position: int, tasks: list[Task] | None = None) -> Goal:
    return Goal(id=gid, name=gid, position=position, description="", tasks=tasks or [])


def enriching_plan() -> Plan:
    return Plan(
        project_id="project-1", id="p1", brief="b", phase=PlanPhase.ENRICHING, goals=[goal("g1", 0)]
    )


class FailingReasoner:
    """enrich_goal raises ReasonerUnavailable; converse is unused here. When
    `succeed_after` failures have happened it returns a task set instead."""

    def __init__(self, *, transient: bool, succeed_after: int | None = None):
        self._transient = transient
        self._succeed_after = succeed_after
        self.calls = 0

    async def converse(self, plan, history, message, mode):  # pragma: no cover
        raise AssertionError("converse not expected")

    async def enrich_goal(self, plan, goal, capabilities):
        self.calls += 1
        if self._succeed_after is not None and self.calls > self._succeed_after:
            return [Task(id="t1", name="t1", position=0, description="")]
        raise ReasonerUnavailable("provider rate limited", transient=self._transient)


def handler(reasoner, env):
    return PlanningHandler(reasoner, env.agents, InMemoryCapabilityRepository(), env.clock)


def test_transient_failure_arms_backoff_gate_and_emits_event(env_factory):
    env = env_factory()
    env.seed(enriching_plan())
    reasoner = FailingReasoner(transient=True)

    signal = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.NOT_READY  # worker releases + sleeps; no hot loop
    stored = env.stored("p1")
    assert stored.phase == PlanPhase.ENRICHING  # still recoverable
    assert stored.planning_attempts == 1
    # default planning retry starts at a durable 30-second provider-safe window
    assert stored.planning_retry_not_before == env.clock.now() + timedelta(seconds=30.0)
    assert env.outbox_types() == ["ReasonerFailed"]


def test_transient_failures_exhaust_budget_then_fail(env_factory):
    env = env_factory()
    env.seed(enriching_plan())
    reasoner = FailingReasoner(transient=True)  # default max_attempts = 3

    signals = [
        asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))
        for _ in range(3)
    ]

    assert signals == [Signal.NOT_READY, Signal.NOT_READY, Signal.FAILED]
    assert env.stored("p1").phase == PlanPhase.FAILED
    # three ReasonerFailed (one per attempt) + the terminal PlanFailed
    assert env.outbox_types() == [
        "ReasonerFailed",
        "ReasonerFailed",
        "ReasonerFailed",
        "PlanFailed",
    ]


def test_permanent_failure_fails_the_plan_immediately(env_factory):
    env = env_factory()
    env.seed(enriching_plan())
    reasoner = FailingReasoner(transient=False)

    signal = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.FAILED
    stored = env.stored("p1")
    assert stored.phase == PlanPhase.FAILED
    assert stored.planning_attempts == 0  # never armed a retry
    assert env.outbox_types() == ["ReasonerFailed", "PlanFailed"]


def test_recovery_clears_the_backoff_gate(env_factory):
    env = env_factory()
    env.seed(enriching_plan())
    reasoner = FailingReasoner(transient=True, succeed_after=1)

    first = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))
    assert first == Signal.NOT_READY
    assert env.stored("p1").planning_retry_not_before is not None

    second = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert second == Signal.CONTINUE
    stored = env.stored("p1")
    assert stored.planning_attempts == 0  # reset on progress
    assert stored.planning_retry_not_before is None  # gate disarmed
    assert [t.name for t in stored.goals[0].tasks] == ["t1"]


def test_claim_predicate_skips_a_plan_until_its_gate_opens(env_factory):
    """The gate is durable: an armed plan is not claimed until the clock passes it —
    identically on the fake and the real SQLite claim predicate."""
    env = env_factory()
    plan = enriching_plan()
    plan.record_planning_retry(env.clock.now() + timedelta(seconds=30))
    env.seed(plan)

    assert env.uow.plans.claim_one_unit("w1", lease_seconds=60) is None  # gated

    env.clock.advance(31)
    claimed = env.uow.plans.claim_one_unit("w1", lease_seconds=60)
    assert claimed is not None and claimed.id == "p1"


def test_a_failed_enrichment_leaves_its_work_behind_for_the_retry(env_factory):
    """The artifact must OUTLIVE the failure that produced it.

    Before this, a rejected submission lived only in the reasoner's in-process
    message list; the raise discarded it, `_start_operation` reused the same
    planning_operations row, and the retry rebuilt its prompt from nothing —
    reproducing the same class of mistake until the budget opened a block.

    Written outside the plan transaction on purpose (the ChatStore rule): a
    record kept so the retry can learn must not roll back with the rollback.
    """
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    class RejectsThenDies:
        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal(self, plan, goal, capabilities):
            raise ReasonerUnavailable(
                "Reasoner session exceeded 4 turns without submitting",
                transient=True,
                partial_artifact={"tasks": [{"allowed_scope": ["implementation"]}]},
                rejection_reasons=("allowed_scope: 'implementation' is a capability id",),
                input_fingerprint="fp-1",
            )

    env = env_factory()
    env.seed(enriching_plan())
    artifacts = InMemoryPlanningArtifactStore()
    planning = PlanningHandler(
        RejectsThenDies(),
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        planning_artifacts=artifacts,
    )

    signal = asyncio.run(planning.handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.NOT_READY  # backoff, as before
    (kept,) = artifacts.latest("p1", "goal_contract", goal_id="g1")
    assert kept.outcome == "rejected"
    assert kept.payload == {"tasks": [{"allowed_scope": ["implementation"]}]}
    assert kept.rejection_reasons == ("allowed_scope: 'implementation' is a capability id",)
    assert kept.input_fingerprint == "fp-1"


def test_a_session_that_produced_nothing_is_still_recorded(env_factory):
    """The most common enrichment failure is a session dying on its turn budget
    without ever submitting — observed live, twice. If that leaves no row, the
    retry is granted no extra turns and dies in exactly the same way.

    The row exists to buy the escalating budget. It carries no payload and no
    reasons, and the replay's outcome filter keeps `abandoned` out of the prompt:
    there is nothing there to learn from, only evidence that this goal is hard.
    """
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    env.seed(enriching_plan())
    artifacts = InMemoryPlanningArtifactStore()
    planning = PlanningHandler(
        FailingReasoner(transient=True),
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        planning_artifacts=artifacts,
    )

    asyncio.run(planning.handle("p1", env.stored("p1"), env.uow))

    kept = artifacts.latest("p1", "goal_contract", goal_id="g1")
    assert kept == []  # no fingerprint on this failure -> nothing addressable


def test_a_budget_exhaustion_records_an_abandoned_attempt(env_factory):
    """With a fingerprint, an empty-handed failure IS recorded."""
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    class DiesOnBudget:
        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal(self, plan, goal, capabilities):
            raise ReasonerUnavailable(
                "Reasoner session exceeded 4 turns without submitting",
                transient=True,
                input_fingerprint="fp-1",
                turns_used=4,
            )

    env = env_factory()
    env.seed(enriching_plan())
    artifacts = InMemoryPlanningArtifactStore()
    planning = PlanningHandler(
        DiesOnBudget(),
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        planning_artifacts=artifacts,
    )

    asyncio.run(planning.handle("p1", env.stored("p1"), env.uow))

    (kept,) = artifacts.latest("p1", "goal_contract", goal_id="g1")
    assert kept.outcome == "abandoned"
    assert kept.payload is None and kept.rejection_reasons == ()
    assert kept.turns_used == 4


def test_a_permanent_planning_failure_still_fails_immediately(env_factory):
    """Waiting cannot fix a model that has no tool-use endpoint."""
    env = env_factory()
    env.seed(enriching_plan())

    asyncio.run(handler(FailingReasoner(transient=False), env).handle("p1", env.stored("p1"), env.uow))

    from agent_orchestrator.domain.entities.planning_artifacts import PlanStatus

    assert env.stored("p1").status == PlanStatus.BLOCKED
