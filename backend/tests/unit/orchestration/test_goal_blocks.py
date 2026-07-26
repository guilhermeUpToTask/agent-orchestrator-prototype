"""Per-goal blocks (domain unfreeze #14 — goal-level parallelism v2): a block
on one goal must never stop an unrelated, independent sibling goal from
progressing, and the plan-wide `status` only becomes BLOCKED when EVERY
non-terminal goal is blocked or transitively depends on one that is."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _goal(
    goal_id: str, position: int, tasks: list[Task], depends_on: list[str] | None = None
) -> Goal:
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


def _block(
    goal_id: str, task_id: str = "t", legal_resolutions: list[str] | None = None
) -> PlanBlock:
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


def test_plan_wide_block_summary_still_surfaces_coexisting_goal_blocks():
    """A plan-wide scalar block is the headline, but coexisting per-goal
    blocks must stay visible in status_reason/legal_actions -- operators
    would otherwise only discover them after resolving the scalar one."""
    plan = _cyclic_plan([_goal("g1", 0, [_task("t1")]), _goal("g2", 1, [_task("t2")])])
    plan.open_block(_block("g1"))
    plan.open_block(
        PlanBlock(
            id="block-plan-wide",
            kind="reasoner_failure",
            explanation="planner down",
            stage="planning",
            legal_resolutions=["retry_planning_stage"],
            created_at=NOW,
        )
    )

    reason = plan.status_reason
    assert reason["kind"] == "block"
    assert reason["code"] == "reasoner_failure"
    assert "1 goal(s) independently blocked" in (reason["message"] or "")

    actions = plan.legal_actions
    assert actions[0] == "retry_planning_stage"  # the scalar block leads
    for resolution in plan.goal_blocks["g1"].legal_resolutions:
        assert resolution in actions


def test_enrichment_reasoner_failure_blocks_only_its_own_goal(env_factory):
    """A per-goal enrichment failure must open a GOAL-scoped block.

    Observed live with 6 independent goals and max_concurrent_goals=4: the 6th
    goal's `enrich_goal_contract` exhausted its turn budget, `_handle_reasoner_failure`
    built a PlanBlock with NO goal_id, `open_block` therefore routed it to the
    plan-wide scalar, and the WHOLE plan flipped to BLOCKED -- stranding four
    siblings that were independently RUNNING at that moment.

    Enrichment is per-goal by construction (`_enrich_one` targets one goal, and
    the PlanningOperation already carries `target_goal_id`), so this is exactly
    the failure mode domain unfreeze #14 removed for execution blocks. Only a
    plan-wide reasoner failure (cycle architecture, target_goal_id=None) may
    still use the scalar.
    """
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.ports import ReasonerUnavailable
    from src.app.testing.fakes import InMemoryCapabilityRepository

    class EnrichFails:
        def __init__(self):
            self.goal_ids = []

        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal_contract(self, plan, goal, capabilities):
            self.goal_ids.append(goal.id)
            raise ReasonerUnavailable(
                "Reasoner session exceeded 4 turns without submitting", transient=False
            )

    env = env_factory()
    plan = _cyclic_plan([_goal("g1", 0, []), _goal("g2", 1, [])])
    plan.retry_policy.max_attempts = 1  # go terminal on the first failure
    env.seed(plan)

    reasoner = EnrichFails()
    handler = PlanningHandler(reasoner, env.agents, InMemoryCapabilityRepository(), env.clock)
    with env.uow:
        loaded = env.uow.plans.get("p1")
    asyncio.run(handler.handle("p1", loaded, env.uow))

    stored = env.stored("p1")
    assert stored.block is None, (
        "a per-goal enrichment failure must not open the plan-wide scalar block"
    )
    assert "g1" in stored.goal_blocks and stored.goal_blocks["g1"].active
    assert stored.goal_blocks["g1"].goal_id == "g1"
    assert "g2" not in stored.goal_blocks
    assert stored.status == PlanStatus.RUNNING, (
        "an independent sibling goal must keep the plan claimable"
    )

    # The next planning tick must skip g1's active goal block and attempt g2.
    # Without that exclusion, it would select g1 again and collide with the
    # already-open block instead of preserving sibling progress.
    with env.uow:
        loaded = env.uow.plans.get("p1")
    asyncio.run(handler.handle("p1", loaded, env.uow))

    stored = env.stored("p1")
    assert reasoner.goal_ids == ["g1", "g2"]
    assert stored.block is None
    assert set(stored.goal_blocks) == {"g1", "g2"}
    assert stored.status == PlanStatus.BLOCKED  # every independent goal is now blocked


def test_capacity_reasoner_failure_keeps_waiting_past_the_attempt_budget(env_factory):
    """A rate-limited provider must not become a block after three attempts.

    Terminality used to be decided from the `transient` flag plus the attempt
    budget, so routine provider throttling -- which OpenRouter surfaces as an
    HTTP 200 with no choices, unconditionally transient -- opened a
    reasoner_failure block on the third failure. Waiting is what resolves
    capacity, so capacity kinds now back off until the outage outlives the
    wall-clock ceiling.
    """
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.ports import ReasonerUnavailable
    from src.app.provider_capacity import ProviderCapacityPolicy
    from src.app.testing.fakes import InMemoryCapabilityRepository
    from src.domain.value_objects.lifecycle import FailureKind

    class RateLimited:
        def __init__(self):
            self.calls = 0

        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal_contract(self, plan, goal, capabilities):
            self.calls += 1
            raise ReasonerUnavailable(
                "provider rejected the request (rate limited)",
                transient=True,
                kind=FailureKind.RATE_LIMIT,
            )

    env = env_factory()
    plan = _cyclic_plan([_goal("g1", 0, [])])
    plan.retry_policy.max_attempts = 2  # the old rule went terminal on attempt 2
    env.seed(plan)

    reasoner = RateLimited()
    handler = PlanningHandler(
        reasoner,
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        ProviderCapacityPolicy(outage_ceiling_seconds=10_000),
    )

    def tick():
        with env.uow:
            loaded = env.uow.plans.get("p1")
        return asyncio.run(handler.handle("p1", loaded, env.uow))

    for _ in range(5):
        tick()
        env.clock.advance(600)

    stored = env.stored("p1")
    assert stored.goal_blocks == {}, "capacity must not open a block inside the ceiling"
    assert stored.block is None
    assert stored.status == PlanStatus.RUNNING
    assert reasoner.calls == 5  # kept trying rather than giving up at 2

    # ...but it is still bounded: past the ceiling it escalates.
    env.clock.advance(20_000)
    tick()
    stored = env.stored("p1")
    assert stored.goal_blocks.get("g1") is not None
    assert stored.goal_blocks["g1"].kind == "reasoner_failure"


def test_tool_error_reasoner_failure_still_blocks_on_the_attempt_budget(env_factory):
    """A model that answers with prose instead of calling the submission tool will
    never be fixed by waiting, so it must keep the ordinary budget. Treating every
    transient failure as capacity would loop forever against an incapable model."""
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.ports import ReasonerUnavailable
    from src.app.provider_capacity import ProviderCapacityPolicy
    from src.app.testing.fakes import InMemoryCapabilityRepository
    from src.domain.value_objects.lifecycle import FailureKind

    class NeverSubmits:
        def __init__(self):
            self.calls = 0

        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal_contract(self, plan, goal, capabilities):
            self.calls += 1
            raise ReasonerUnavailable(
                "Reasoner session exceeded 4 turns without submitting",
                transient=True,
                kind=FailureKind.TOOL_ERROR,
            )

    env = env_factory()
    plan = _cyclic_plan([_goal("g1", 0, [])])
    plan.retry_policy.max_attempts = 2
    env.seed(plan)

    reasoner = NeverSubmits()
    handler = PlanningHandler(
        reasoner,
        env.agents,
        InMemoryCapabilityRepository(),
        env.clock,
        ProviderCapacityPolicy(outage_ceiling_seconds=10_000),
    )

    def tick():
        with env.uow:
            loaded = env.uow.plans.get("p1")
        return asyncio.run(handler.handle("p1", loaded, env.uow))

    tick()
    env.clock.advance(600)
    assert env.stored("p1").goal_blocks == {}  # first failure backs off
    tick()

    stored = env.stored("p1")
    assert stored.goal_blocks.get("g1") is not None
    assert stored.goal_blocks["g1"].kind == "reasoner_failure"
    assert reasoner.calls == 2  # budget respected, not waited out


def test_planning_backoff_honors_provider_retry_after(env_factory):
    """A provider-supplied Retry-After is a floor on the gate: polling sooner than
    the provider asked just earns another refusal."""
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.ports import ReasonerUnavailable
    from src.app.testing.fakes import InMemoryCapabilityRepository
    from src.domain.value_objects.lifecycle import FailureKind

    class RetryAfter:
        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal_contract(self, plan, goal, capabilities):
            raise ReasonerUnavailable(
                "rate limited",
                transient=True,
                kind=FailureKind.RATE_LIMIT,
                retry_after_seconds=4_242.0,
            )

    env = env_factory()
    plan = _cyclic_plan([_goal("g1", 0, [])])
    env.seed(plan)

    handler = PlanningHandler(RetryAfter(), env.agents, InMemoryCapabilityRepository(), env.clock)
    with env.uow:
        loaded = env.uow.plans.get("p1")
    asyncio.run(handler.handle("p1", loaded, env.uow))

    stored = env.stored("p1")
    assert stored.planning_retry_not_before == env.clock.now() + timedelta(seconds=4_242.0)


def test_cycle_architecture_reasoner_failure_remains_plan_wide(env_factory):
    """An operation without a goal target must retain scalar-block semantics."""
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.ports import ReasonerUnavailable
    from src.app.testing.fakes import InMemoryCapabilityRepository
    from src.domain.entities.planning_artifacts import IntentProposal, ProposalKind

    class ArchitectureFails:
        async def architect_cycle(self, plan):
            raise ReasonerUnavailable("cycle architect unavailable", transient=False)

        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

    env = env_factory()
    plan = _cyclic_plan([_goal("g1", 0, [])])
    plan.intent_proposal = IntentProposal(
        id="intent-2",
        kind=ProposalKind.REPLAN,
        base_plan_version=plan.version,
        source_cycle_id="cycle-1",
        objective="replace the active cycle",
        approved_at=env.clock.now(),
    )
    plan.retry_policy.max_attempts = 1
    env.seed(plan)

    handler = PlanningHandler(
        ArchitectureFails(), env.agents, InMemoryCapabilityRepository(), env.clock
    )
    with env.uow:
        loaded = env.uow.plans.get("p1")
    asyncio.run(handler.handle("p1", loaded, env.uow))

    stored = env.stored("p1")
    assert stored.block is not None and stored.block.active
    assert stored.block.goal_id is None
    assert stored.goal_blocks == {}
    assert stored.status == PlanStatus.BLOCKED


def test_unsatisfiable_role_binding_opens_a_block_instead_of_poisoning_the_worker(env_factory):
    """Observed live: enrichment produced a contract whose tasks needed
    `test_authoring`, no registered agent covered it, and the worker crashed with
    an unhandled `RoleUnsatisfiableError` — six times, once per tick.

    The handler catches `ValueError`, which is what role resolution used to
    raise. It now raises `RoleUnsatisfiableError`, a `DomainError` that is NOT a
    `ValueError`, so the designed `agent_capability` block stopped being reachable
    and the plan became a poison pill instead: the exact 1Hz re-dispatch storm
    `_block_on_unpromotable_goal` exists to prevent.
    """
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.testing.fakes import InMemoryCapabilityRepository
    from src.domain.entities.capability import Capability
    from src.domain.entities.execution_contracts import (
        ContractCriterion,
        GoalContract,
        TaskContract,
        VerificationStrategy,
    )

    contract = TaskContract(
        id="t1",
        position=0,
        objective="do it",
        acceptance_criteria=[ContractCriterion(id="t-1", description="works")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["src/", "tests/"],
        verification_commands=["pytest -q"],
        verification_strategy=VerificationStrategy.TDD,
        required_capabilities=["test_authoring"],
    )

    class Enriches:
        async def converse(self, plan, history, message, mode):  # pragma: no cover
            raise AssertionError("unused")

        async def enrich_goal_contract(self, plan, goal, capabilities):
            return GoalContract(
                id=goal.id,
                objective="ship",
                acceptance_criteria=[ContractCriterion(id="g-1", description="shipped")],
                tasks=[contract],
                frozen_at=NOW,
            )

    env = env_factory()
    env.seed(_cyclic_plan([_goal("g1", 0, [])]))
    # the registry covers nothing the contract asks for
    handler = PlanningHandler(
        Enriches(),
        env.agents,
        InMemoryCapabilityRepository(
            [Capability(id="test_authoring", name="test authoring", description="")]
        ),
        env.clock,
    )

    def tick():
        with env.uow:
            loaded = env.uow.plans.get("p1")
        return asyncio.run(handler.handle("p1", loaded, env.uow))

    tick()  # must not raise

    stored = env.stored("p1")
    block = stored.goal_blocks.get("g1") or stored.block
    assert block is not None and block.active
    assert block.kind == "agent_capability"
    assert "test_author" in block.explanation
    # and a second tick must stay quiet rather than re-raising every poll
    tick()


def test_a_per_goal_reasoner_failure_is_reachable_by_its_own_resolution(env_factory):
    """`planning_handler` files a `reasoner_failure` into `goal_blocks` when it
    knows which goal was being enriched, and the block advertises `retry_stage`.
    `Plan.retry_planning_stage` read only the scalar `self.block`, so that
    resolution 422'd — the block advertised the one action that could not reach
    it. The plan-wide case (cycle architecture, no goal) must keep working.
    """
    import pytest

    from src.domain.errors.planning_errors import InvalidEditError

    plan = _cyclic_plan([_goal("g1", 0, [])])
    plan.open_block(
        PlanBlock(
            id="b1",
            kind="reasoner_failure",
            explanation="Reasoner session exceeded 8 turns without submitting",
            stage="goal_contract",
            goal_id="g1",
            legal_resolutions=["retry_stage", "start_replan"],
            created_at=NOW,
        )
    )
    assert plan.block is None and plan.goal_blocks["g1"].active  # routed per-goal

    plan.retry_planning_stage(NOW)

    assert plan.goal_blocks["g1"].resolution == "retry_stage"
    assert plan.goal_blocks["g1"].active is False
    assert plan.planning_attempts == 0  # the backoff gate is cleared too

    # and a plan with nothing retryable still refuses
    with pytest.raises(InvalidEditError, match="not blocked on a retryable planning stage"):
        _cyclic_plan([_goal("g2", 0, [])]).retry_planning_stage(NOW)
