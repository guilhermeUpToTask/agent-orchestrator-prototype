"""PlanningHandler — owns the WORKER-DRIVEN planning phases: ARCHITECTURE and
ENRICHING (the autonomous steps of the phase-machine driver).

The conversational phases (DISCOVERY, REPLANNING) do NOT belong to this
handler — they are chat/API-driven (the driver model): each user message
advances them via the conversation use cases, and the claim predicate makes
them invisible to workers. Reaching this handler in one of them is a defensive
anomaly and simply pauses.

ARCHITECTURE — a deliberate NO-LLM PASSTHROUGH (evaluated for the prototype):
the old system needed an autonomous architecture run because its discovery
produced only a brief; here the discovery/replanning CONVERSATION commits the
user-agreed goal roadmap itself, so an LLM re-structuring pass would be
redundant — and risks mangling a goal set the user just signed off on. The
phase stays in the frozen enum (REPLANNING re-enters through it; it is a free
crash checkpoint between the commit and enrichment) and THIS METHOD is the
seam if a real structuring pass ever returns.

ENRICHING — the JIT step: ONE task-less goal per handle() call. The reasoner
breaks that goal into a small ordered set of plain executable tasks; the
transaction re-reads, re-guards, and commits that single goal's tasks, then
returns CONTINUE — a crash between goals resumes exactly where it stopped, and
a crash between the LLM call and the commit is absorbed by the idempotency
guard (a goal that already has tasks is never re-enriched — goals populated by
the user in chat are likewise skipped entirely). When no task-less goal
remains, agents bind and the plan pauses at the pre-execution gate.

Choreography per step (same shape as the execution handler's crash safety):
the reasoner call — the LLM side effect — happens OUTSIDE any transaction; the
transaction then re-reads the plan, re-checks the phase (tolerant of a racing
human command), writes, and commits state + events atomically via the outbox.
"""
from __future__ import annotations

from datetime import timedelta

from src.domain.aggregates.planner_orchestrator import (
    Plan,
    PlanPhase,
    WORKER_PLANNING_PHASES,
)
from src.domain.entities.goal import Goal
from src.domain.events.outbox import (
    AgentFellBackToDefault,
    PhaseAdvanced,
    PlanFailed,
    ReasonerFailed,
)
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.repositories.capability_repo import CapabilityRepository

from src.app.handlers.base import Signal
from src.app.ports import Clock, Reasoner, ReasonerUnavailable, UnitOfWork


def _next_unenriched(plan: Plan) -> Goal | None:
    """First non-terminal goal (position order) still without tasks."""
    candidates = [g for g in plan.goals if not g.is_terminal and not g.tasks]
    return min(candidates, key=lambda g: g.position, default=None)


class PlanningHandler:
    def __init__(
        self,
        reasoner: Reasoner,
        agents: AgentRepository,
        capabilities: CapabilityRepository,
        clock: Clock,
    ) -> None:
        self._reasoner = reasoner
        self._agents = agents
        self._capabilities = capabilities
        self._clock = clock

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        if plan.phase == PlanPhase.ARCHITECTURE:
            return await self._architect(plan_id, plan, uow)
        if plan.phase == PlanPhase.ENRICHING:
            return await self._enrich(plan_id, plan, uow)
        # DISCOVERY / REPLANNING are conversational — never worker-driven.
        return Signal.PAUSED

    async def _architect(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        """No-LLM passthrough (see module docstring): the conversation already
        committed the roadmap; validate the phase and flow into ENRICHING."""
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase != PlanPhase.ARCHITECTURE or plan.paused:
                return Signal.PAUSED  # raced by a human command; theirs wins
            plan.advance_phase(PlanPhase.ENRICHING)
            plan.bump_version()
            uow.outbox.add(
                PhaseAdvanced(
                    plan_id=plan_id,
                    from_phase=PlanPhase.ARCHITECTURE.value,
                    to_phase=PlanPhase.ENRICHING.value,
                )
            )
            uow.plans.save(plan)
        return Signal.CONTINUE

    async def _enrich(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        if plan.paused:
            return Signal.PAUSED  # don't spend an LLM call on a paused plan
        target = _next_unenriched(plan)
        if target is not None:
            return await self._enrich_one(plan_id, target, plan, uow)
        return await self._bind_and_gate(plan_id, uow)

    async def _enrich_one(
        self, plan_id: str, target: Goal, plan: Plan, uow: UnitOfWork
    ) -> Signal:
        """Populate ONE goal's tasks, commit, CONTINUE (the JIT checkpoint)."""
        try:
            tasks = await self._reasoner.enrich_goal(  # LLM, outside any txn
                plan, target, self._capabilities.list()
            )
        except ReasonerUnavailable as exc:
            # The reasoner is down (rate limit / upstream error / bad config). Arm
            # the durable backoff gate or fail the plan — and surface it (outbox ->
            # SSE) instead of letting it propagate to a silent worker.tick_failed loop.
            return self._handle_reasoner_failure(plan_id, exc, uow)
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase != PlanPhase.ENRICHING or plan.paused:
                return Signal.PAUSED
            fresh = next((g for g in plan.goals if g.id == target.id), None)
            if fresh is None or fresh.tasks:
                # idempotency guard: a crash after commit (or a racing worker)
                # already populated this goal — never enrich twice
                return Signal.CONTINUE
            goals = [
                g.model_copy(deep=True) for g in plan.goals if not g.is_terminal
            ]
            for goal in goals:
                if goal.id == target.id:
                    goal.tasks = [
                        t.model_copy(update={"position": i})
                        for i, t in enumerate(tasks)
                    ]
            plan.set_iteration_goals(goals)
            plan.clear_planning_retry()  # progressed: disarm any prior backoff gate
            plan.bump_version()
            uow.plans.save(plan)
        return Signal.CONTINUE

    def _handle_reasoner_failure(
        self, plan_id: str, exc: ReasonerUnavailable, uow: UnitOfWork
    ) -> Signal:
        """A reasoner failure during ENRICHING: re-read + re-guard, then either arm
        the plan-level backoff gate (transient, budget left) or fail the plan
        (permanent, or budget exhausted). Emits a ReasonerFailed event either way so
        the frontend sees it; the transient path returns NOT_READY so the worker
        releases and sleeps (the gate blocks re-claim until it opens)."""
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase not in WORKER_PLANNING_PHASES or plan.paused:
                return Signal.PAUSED  # raced by a human command; theirs wins
            phase = plan.phase.value
            next_attempt = plan.planning_attempts + 1
            terminal = not exc.transient or next_attempt >= plan.retry_policy.max_attempts

            if terminal:
                plan.fail_plan()
                plan.bump_version()
                uow.outbox.add(
                    ReasonerFailed(
                        plan_id=plan_id, phase=phase, reason=exc.reason,
                        transient=False, retry_at=None,
                    )
                )
                uow.outbox.add(PlanFailed(plan_id=plan_id, reason=exc.reason))
                uow.plans.save(plan)
                return Signal.FAILED

            delay = plan.retry_policy.backoff_for(next_attempt + 1)
            not_before = (
                self._clock.now() + timedelta(seconds=delay) if delay > 0 else None
            )
            plan.record_planning_retry(not_before)
            plan.bump_version()
            uow.outbox.add(
                ReasonerFailed(
                    plan_id=plan_id, phase=phase, reason=exc.reason, transient=True,
                    retry_at=not_before.isoformat() if not_before else None,
                )
            )
            uow.plans.save(plan)
        return Signal.NOT_READY

    async def _bind_and_gate(self, plan_id: str, uow: UnitOfWork) -> Signal:
        """Every goal carries tasks: bind agents and pause at the gate."""
        agents = self._agents.list()
        default_id = self._agents.default_agent_id()
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase != PlanPhase.ENRICHING or plan.paused:
                return Signal.PAUSED
            fell_back = plan.bind_agents(agents, default_id)
            plan.advance_phase(PlanPhase.AWAITING_REVIEW)
            plan.bump_version()
            for task_id in fell_back:
                task = next(t for g in plan.goals for t in g.tasks if t.id == task_id)
                uow.outbox.add(
                    AgentFellBackToDefault(
                        plan_id=plan_id,
                        task_id=task_id,
                        required_capabilities=list(task.required_capabilities),
                    )
                )
            uow.outbox.add(
                PhaseAdvanced(
                    plan_id=plan_id,
                    from_phase=PlanPhase.ENRICHING.value,
                    to_phase=PlanPhase.AWAITING_REVIEW.value,
                )
            )
            uow.plans.save(plan)
        return Signal.PAUSED  # the pre-execution gate is next: release the plan
