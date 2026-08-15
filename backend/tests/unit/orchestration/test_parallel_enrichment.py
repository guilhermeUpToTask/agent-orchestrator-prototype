"""Ready goals are enriched CONCURRENTLY — P8.6 Task 2.

Enrichment is JIT by design and was also strictly serial by accident: one
`handle()` tick picked the single earliest ready goal, ran a whole reasoner
session, committed, and only then let the next tick start the next goal. The
readiness set that makes the fan-out safe (`ready_goal_ids`) was already
computed and already honoured by the execution loop — enrichment simply did not
use it. Five independent goals therefore cost five sequential planning sessions:
measured at ~25 minutes of pure sequencing in the 2026-08-09 latency analysis,
against a floor of one session.

The property under test is *overlap*, not speed, because speed is not
observable in a unit test and overlap is exactly what was missing. The probe
reasoner below blocks each session until its peers arrive, so a serial handler
can never satisfy it: with N goals ready it records a peak in-flight count of 1
and this file goes red.

What must NOT change is the invariant that makes JIT enrichment worth having: a
goal whose `depends_on` is unmet is still not enriched, however much
parallelism is available. Enriching early would freeze a contract against a
repository state that the goal it depends on has not produced yet.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from praxis_orchestrator.app.handlers.base import Signal
from praxis_orchestrator.app.handlers.planning_handler import PlanningHandler
from praxis_orchestrator.app.testing.fakes import InMemoryCapabilityRepository
from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.agent_spec import AgentSpec
from praxis_orchestrator.domain.entities.capability import Capability
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.planning_artifacts import Cycle, PlanStatus
from praxis_orchestrator.domain.policies.retry_policies import RetryPolicy
from praxis_orchestrator.infra.reasoner.stub_reasoner import StubReasoner

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)

# How long a session waits for its peers before concluding it is alone. Only
# ever paid on FAILURE (a serial handler waits it out once); a concurrent
# handler releases the moment the last session arrives.
_PEER_DEADLINE_SECONDS = 1.0


def _agent(agent_id: str, capability: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role=capability,
        model_role="smart",
        instructions="",
        capabilities=[Capability(id=capability, name=capability, description="")],
        default_retry=RetryPolicy(),
    )


ROSTER = [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")]


class OverlapProbeReasoner(StubReasoner):
    """A reasoner that can only finish quickly if its sessions overlap.

    Each `enrich_goal_contract` announces itself and then waits for the arrival
    of `expected` peers. Serial enrichment deadlocks against that (each session
    is the only one in flight), so it falls through on a timeout and leaves
    `peak_inflight == 1` — the signature of the defect.
    """

    def __init__(self, expected: int) -> None:
        super().__init__()
        self._expected = expected
        self._all_arrived = asyncio.Event()
        self.inflight = 0
        self.peak_inflight = 0
        self.enriched_goal_ids: list[str] = []

    async def enrich_goal_contract(self, plan, goal, capabilities):  # type: ignore[no-untyped-def]
        self.enriched_goal_ids.append(goal.id)
        self.inflight += 1
        self.peak_inflight = max(self.peak_inflight, self.inflight)
        try:
            if self.inflight >= self._expected:
                self._all_arrived.set()
            try:
                await asyncio.wait_for(
                    self._all_arrived.wait(), timeout=_PEER_DEADLINE_SECONDS
                )
            except asyncio.TimeoutError:
                pass  # alone: let the assertion report it, don't hang the suite
            return await super().enrich_goal_contract(plan, goal, capabilities)
        finally:
            self.inflight -= 1


def _plan_with_goals(goals: list[Goal]) -> Plan:
    return Plan(
        id="plan-1",
        project_id="project-1",
        brief="ship",
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=goals,
                started_at=NOW,
            )
        ],
    )


def _independent_goals(count: int) -> list[Goal]:
    return [
        Goal(id=f"goal-{index}", name=f"Goal {index}", position=index, description="work")
        for index in range(count)
    ]


def _handler(reasoner, env, max_concurrent_enrichment: int = 4) -> PlanningHandler:
    return PlanningHandler(
        reasoner,
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        max_concurrent_enrichment=max_concurrent_enrichment,
    )


def test_independent_ready_goals_are_enriched_in_one_overlapping_pass(env_factory) -> None:
    """The headline: N ready goals cost one session's wall-clock, not N."""
    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    plan = _plan_with_goals(_independent_goals(3))
    env.seed(plan)
    reasoner = OverlapProbeReasoner(expected=3)

    signal = asyncio.run(_handler(reasoner, env).handle(plan.id, plan, env.uow))

    assert signal == Signal.CONTINUE
    assert reasoner.peak_inflight == 3, (
        "enrichment sessions did not overlap: the handler is still waiting for "
        f"each goal to commit before starting the next (peak={reasoner.peak_inflight})"
    )
    enriched = env.stored(plan.id)
    assert enriched.active_cycle is not None
    for goal in enriched.active_cycle.goals:
        assert goal.tasks, f"{goal.id} was left unenriched by the parallel pass"
        assert goal.contract is not None


def test_every_goal_that_was_enriched_in_parallel_still_got_its_role_bindings(
    env_factory,
) -> None:
    """Each goal commits in its own transaction, so a concurrent pass must not
    let one goal's commit overwrite another's — the version-per-commit path is
    where a naive `asyncio.gather` over a shared aggregate loses work."""
    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    plan = _plan_with_goals(_independent_goals(3))
    env.seed(plan)

    asyncio.run(
        _handler(OverlapProbeReasoner(expected=3), env).handle(plan.id, plan, env.uow)
    )

    enriched = env.stored(plan.id)
    assert enriched.active_cycle is not None
    for goal in enriched.active_cycle.goals:
        for task in goal.tasks:
            assert task.role_agent_ids == {
                "test_author": "test-author",
                "implementer": "implementer",
            }
            assert task.agent_id == "implementer"


def test_the_pass_is_bounded_by_max_concurrent_enrichment(env_factory) -> None:
    """Unbounded fan-out would open one provider session per ready goal and
    trip the very capacity limits this phase exists to stop waiting on."""
    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    plan = _plan_with_goals(_independent_goals(5))
    env.seed(plan)
    # Expect 2: the bound. If the handler ignored it and started all 5, the
    # probe would report 5 and the assertion below catches it.
    reasoner = OverlapProbeReasoner(expected=2)

    asyncio.run(_handler(reasoner, env, max_concurrent_enrichment=2).handle(
        plan.id, plan, env.uow
    ))

    assert reasoner.peak_inflight == 2
    enriched = env.stored(plan.id)
    assert enriched.active_cycle is not None
    assert sum(1 for goal in enriched.active_cycle.goals if goal.tasks) == 2


def test_a_dependency_blocked_goal_is_never_enriched_early(env_factory) -> None:
    """The invariant parallelism must not buy its way past. Goal 0 depends on a
    goal that is not DONE; goals 1 and 2 are independent. Only the latter two
    may be enriched, concurrently, and goal 0 must stay untouched."""
    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    blocked = Goal(
        id="goal-0",
        name="Goal 0",
        position=0,
        description="waits on an unmet dependency",
        depends_on=["never-done"],
    )
    plan = _plan_with_goals([blocked, *_independent_goals(3)[1:]])
    env.seed(plan)
    reasoner = OverlapProbeReasoner(expected=2)

    asyncio.run(_handler(reasoner, env).handle(plan.id, plan, env.uow))

    assert "goal-0" not in reasoner.enriched_goal_ids
    assert sorted(reasoner.enriched_goal_ids) == ["goal-1", "goal-2"]
    enriched = env.stored(plan.id)
    assert enriched.active_cycle is not None
    goals_by_id = {goal.id: goal for goal in enriched.active_cycle.goals}
    assert not goals_by_id["goal-0"].tasks
    assert goals_by_id["goal-1"].tasks
    assert goals_by_id["goal-2"].tasks


def test_one_goals_failed_session_does_not_strand_the_sibling_beside_it(
    env_factory,
) -> None:
    """Sibling isolation, which parallelism makes sharper rather than weaker.

    Serially, a sibling survived a peer's enrichment failure by not having been
    attempted yet — true, but only because it was still queued. Concurrently
    both sessions are genuinely in flight at once, so this asserts the real
    property: the failing goal takes a GOAL-scoped block, the succeeding goal
    commits its contract in the same pass, the plan-wide scalar block stays
    empty, and the plan stays claimable for the work that is still viable.
    """
    from praxis_orchestrator.app.ports import ReasonerUnavailable

    class FailsOneGoal(OverlapProbeReasoner):
        async def enrich_goal_contract(self, plan, goal, capabilities):  # type: ignore[no-untyped-def]
            contract = await super().enrich_goal_contract(plan, goal, capabilities)
            if goal.id == "goal-0":
                raise ReasonerUnavailable(
                    "Reasoner session exceeded its turns without submitting",
                    transient=False,
                )
            return contract

    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    plan = _plan_with_goals(_independent_goals(2))
    plan.retry_policy.max_attempts = 1  # go terminal on the first failure
    env.seed(plan)
    reasoner = FailsOneGoal(expected=2)

    signal = asyncio.run(_handler(reasoner, env).handle(plan.id, plan, env.uow))

    assert reasoner.peak_inflight == 2  # both really were in flight together
    assert signal == Signal.CONTINUE, "the goal that succeeded is progress"
    stored = env.stored(plan.id)
    assert stored.block is None, (
        "a per-goal enrichment failure must not open the plan-wide scalar block"
    )
    assert set(stored.goal_blocks) == {"goal-0"}
    assert stored.goal_blocks["goal-0"].active
    assert stored.status == PlanStatus.RUNNING, (
        "an independently enriched sibling must keep the plan claimable"
    )
    goals_by_id = {goal.id: goal for goal in stored.active_cycle.goals}  # type: ignore[union-attr]
    assert not goals_by_id["goal-0"].tasks
    assert goals_by_id["goal-1"].tasks


def test_a_single_ready_goal_still_enriches_without_a_peer_to_wait_for(
    env_factory,
) -> None:
    """The degenerate case has to stay a plain sequential enrichment — the
    fan-out must not introduce a wait for peers that will never arrive."""
    env = env_factory(agents=ROSTER, default_agent_id="implementer")
    plan = _plan_with_goals(_independent_goals(1))
    env.seed(plan)
    reasoner = OverlapProbeReasoner(expected=1)

    signal = asyncio.run(_handler(reasoner, env).handle(plan.id, plan, env.uow))

    assert signal == Signal.CONTINUE
    assert reasoner.peak_inflight == 1
    enriched = env.stored(plan.id)
    assert enriched.active_cycle is not None
    assert enriched.active_cycle.goals[0].tasks
