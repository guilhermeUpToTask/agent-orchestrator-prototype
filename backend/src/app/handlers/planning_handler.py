"""PlanningHandler — owns durable worker-driven cyclic planning operations.

The conversational phases (DISCOVERY, REPLANNING) do NOT belong to this
handler — they are chat/API-driven (the driver model): each user message
advances them via the conversation use cases, and the claim predicate makes
them invisible to workers. Reaching this handler in one of them is a defensive
anomaly and simply pauses.

For a cyclic plan an approved intent produces one versioned CycleDraft through
`architect_cycle`; after draft approval, only the earliest nonterminal goal is
expanded through `enrich_goal_contract`. Each LLM call has a persisted
PlanningOperation (purpose, target goal, liveness, counts, status and safe
failure evidence), and each artifact commit is idempotently re-guarded.

The legacy ARCHITECTURE/ENRICHING branches remain quarantined for migrated
compatibility plans. New project plans cannot enter that enrich-all lifecycle.

Choreography per step (same shape as the execution handler's crash safety):
the reasoner call — the LLM side effect — happens OUTSIDE any transaction; the
transaction then re-reads the plan, re-checks the phase (tolerant of a racing
human command), writes, and commits state + events atomically via the outbox.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from src.domain.aggregates.planner_orchestrator import (
    Plan,
    PlanPhase,
    WORKER_PLANNING_PHASES,
)
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    CycleDraft,
    PlanBlock,
    PlanStatus,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.entities.task import Task
from src.domain.events.outbox import (
    AgentFellBackToDefault,
    CycleDrafted,
    PhaseAdvanced,
    PlanBlocked,
    PlanFailed,
    ReasonerFailed,
    ReviewGateOpened,
)
from src.domain.factories.identity import new_id
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.repositories.capability_repo import CapabilityRepository
from src.domain.services.agent_role_resolution import resolve_task_role_agents
from src.domain.services.navigation import ready_goal_ids

from src.app.provider_capacity import CAPACITY_KINDS, ProviderCapacityPolicy
from src.app.handlers.base import Signal
from src.app.execution_records import PlanningOperation, PlanningOperationStatus
from src.app.ports import Clock, Reasoner, ReasonerUnavailable, UnitOfWork


def _next_unenriched(plan: Plan, now: datetime) -> Goal | None:
    """First non-terminal, dependency-ready goal (position order) still
    without tasks — dependency-readiness is required so a goal stuck behind an
    unmet `depends_on` never starves an independently-ready later goal from
    being JIT-enriched (goal-parallelism fan-out, ADR-001)."""
    ready_ids = ready_goal_ids(plan.execution_goals, now)
    blocked_goal_ids = {goal_id for goal_id, block in plan.goal_blocks.items() if block.active}
    candidates = [
        goal
        for goal in plan.execution_goals
        if goal.id in ready_ids and goal.id not in blocked_goal_ids and not goal.tasks
    ]
    return min(candidates, key=lambda g: g.position, default=None)


class PlanningHandler:
    def __init__(
        self,
        reasoner: Reasoner,
        agents: AgentRepository,
        capabilities: CapabilityRepository,
        clock: Clock,
        capacity: ProviderCapacityPolicy | None = None,
    ) -> None:
        self._reasoner = reasoner
        self._agents = agents
        self._capabilities = capabilities
        self._clock = clock
        # Same ceilings execution uses, so planning and execution ride out a
        # provider outage for the same length of time instead of one of them
        # escalating first and blocking the plan the other was patiently waiting on.
        self._capacity = capacity or ProviderCapacityPolicy()

    def _start_operation(
        self,
        plan_id: str,
        purpose: str,
        uow: UnitOfWork,
        target_goal_id: str | None = None,
    ) -> PlanningOperation:
        now = self._clock.now()
        with uow:
            operation = uow.executions.find_active_planning_operation(
                plan_id, purpose, target_goal_id
            )
            if operation is None:
                operation = PlanningOperation(
                    id=str(uuid4()),
                    plan_id=plan_id,
                    purpose=purpose,
                    target_goal_id=target_goal_id,
                    status=PlanningOperationStatus.STARTED,
                    created_at=now,
                    updated_at=now,
                    started_at=now,
                    last_liveness_at=now,
                )
                uow.executions.add_planning_operation(operation)
            else:
                operation = replace(
                    operation,
                    status=PlanningOperationStatus.STARTED,
                    updated_at=now,
                    started_at=operation.started_at or now,
                    completed_at=None,
                    last_liveness_at=now,
                    retry_at=None,
                    failure_kind=None,
                    safe_message=None,
                )
                uow.executions.update_planning_operation(operation)
        return operation

    def _finish_operation(
        self,
        uow: UnitOfWork,
        operation: PlanningOperation,
        status: PlanningOperationStatus,
        *,
        failure_kind: str | None = None,
        safe_message: str | None = None,
        retry_at: object | None = None,
    ) -> None:
        now = self._clock.now()
        uow.executions.update_planning_operation(
            replace(
                operation,
                status=status,
                updated_at=now,
                completed_at=(
                    now
                    if status in {PlanningOperationStatus.COMMITTED, PlanningOperationStatus.FAILED}
                    else None
                ),
                last_liveness_at=now,
                model_request_count=operation.model_request_count + 1,
                failure_kind=failure_kind,
                safe_message=safe_message,
                retry_at=retry_at,  # type: ignore[arg-type]
            )
        )

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        # An approved intent with no cycle_draft yet always needs
        # architect_cycle next, checked BEFORE active_cycle: a REPLAN's
        # SOURCE cycle stays `active_cycle` (CycleStatus.ACTIVE) for the
        # entire drafting window (it is only superseded when the
        # replacement activates — source-preserving replan, see
        # docs/architecture/plan-lifecycle.md), so checking active_cycle
        # first would route every tick to `_enrich` on the SOURCE cycle's
        # already-enriched goals instead of drafting the replacement,
        # leaving the approved replan intent permanently stuck (found via a
        # real walkthrough: a real reasoner replan on a plan with an
        # execution-blocked source cycle never reached architect_cycle at
        # all, and the source cycle's failed goal kept getting re-selected
        # and re-blocked instead).
        if (
            plan.status == PlanStatus.RUNNING
            and plan.intent_proposal is not None
            and plan.intent_proposal.approved_at is not None
            and plan.cycle_draft is None
        ):
            return await self._architect_cycle(plan_id, plan, uow)
        if plan.active_cycle is not None:
            return await self._enrich(plan_id, plan, uow)
        if plan.phase == PlanPhase.ARCHITECTURE:
            return await self._architect(plan_id, plan, uow)
        if plan.phase == PlanPhase.ENRICHING:
            return await self._enrich(plan_id, plan, uow)
        # DISCOVERY / REPLANNING are conversational — never worker-driven.
        return Signal.PAUSED

    async def _architect_cycle(
        self,
        plan_id: str,
        plan: Plan,
        uow: UnitOfWork,
    ) -> Signal:
        proposal = plan.intent_proposal
        assert proposal is not None
        operation = self._start_operation(plan_id, "cycle_architecture", uow)
        try:
            outlines = await self._reasoner.architect_cycle(plan)
            # This executes duplicate/unknown/self/cycle validation before any
            # accepted plan state is mutated.
            candidate = CycleDraft(
                id=new_id(),
                intent_proposal_id=proposal.id,
                base_plan_version=plan.version,
                source_cycle_id=proposal.source_cycle_id,
                goals=outlines,
                unfinished_source_treatment=(
                    "supersede unfinished source work atomically on approval"
                    if proposal.source_cycle_id is not None
                    else None
                ),
            )
        except ReasonerUnavailable as exc:
            return self._handle_reasoner_failure(plan_id, exc, uow, operation)

        with uow:
            fresh = uow.plans.get(plan_id)
            approved = fresh.intent_proposal
            if (
                fresh.status != PlanStatus.RUNNING
                or approved is None
                or approved.id != proposal.id
                or approved.revision != proposal.revision
                or fresh.version != plan.version
            ):
                self._finish_operation(
                    uow,
                    operation,
                    PlanningOperationStatus.FAILED,
                    failure_kind="stale_planning_state",
                    safe_message="Planning state changed before the roadmap could commit.",
                )
                return Signal.PAUSED
            gate = ReviewGate(
                id=new_id(),
                subject_type=ReviewSubjectType.CYCLE_DRAFT,
                subject_id=candidate.id,
                subject_revision=candidate.revision,
                allowed_decisions=["approve", "edit", "cancel"],
                continuation="Approve the generated CycleDraft to activate execution.",
            )
            fresh.submit_cycle_draft(candidate, gate)
            fresh.bump_version()
            uow.outbox.add(CycleDrafted(plan_id=plan_id, draft_id=candidate.id, revision=1))
            uow.outbox.add(
                ReviewGateOpened(
                    plan_id=plan_id,
                    gate_id=gate.id,
                    subject_type=gate.subject_type.value,
                    subject_id=candidate.id,
                    subject_revision=1,
                )
            )
            self._finish_operation(uow, operation, PlanningOperationStatus.COMMITTED)
            uow.plans.save(fresh)
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
        target = _next_unenriched(plan, self._clock.now())
        if target is not None:
            return await self._enrich_one(plan_id, target, plan, uow)
        return await self._bind_and_gate(plan_id, uow)

    async def _enrich_one(self, plan_id: str, target: Goal, plan: Plan, uow: UnitOfWork) -> Signal:
        """Populate ONE goal's tasks, commit, CONTINUE (the JIT checkpoint)."""
        operation = self._start_operation(plan_id, "goal_contract", uow, target_goal_id=target.id)
        try:
            cyclic = plan.active_cycle is not None
            if cyclic:
                contract = await self._reasoner.enrich_goal_contract(
                    plan, target, self._capabilities.list()
                )
                contract = contract.model_copy(update={"frozen_at": self._clock.now()})
                tasks = [
                    Task(
                        id=item.id,
                        name=item.objective,
                        position=item.position,
                        description=item.objective,
                        revision=item.revision,
                        required_capabilities=list(item.required_capabilities),
                        contract=item,
                    )
                    for item in contract.tasks
                ]
            else:
                contract = None
                tasks = await self._reasoner.enrich_goal(plan, target, self._capabilities.list())
        except ReasonerUnavailable as exc:
            # The reasoner is down (rate limit / upstream error / bad config). Arm
            # the durable backoff gate or fail the plan — and surface it (outbox ->
            # SSE) instead of letting it propagate to a silent worker.tick_failed loop.
            return self._handle_reasoner_failure(plan_id, exc, uow, operation)
        with uow:
            plan = uow.plans.get(plan_id)
            cyclic = plan.active_cycle is not None
            if plan.paused or plan.pause_requested:
                self._finish_operation(
                    uow,
                    operation,
                    PlanningOperationStatus.FAILED,
                    failure_kind="planning_interrupted",
                    safe_message="Planning stopped because the plan was paused.",
                )
                return Signal.PAUSED
            if not cyclic and plan.phase != PlanPhase.ENRICHING:
                self._finish_operation(
                    uow,
                    operation,
                    PlanningOperationStatus.FAILED,
                    failure_kind="stale_planning_state",
                    safe_message="Planning state changed before the goal contract could commit.",
                )
                return Signal.PAUSED
            fresh = next((g for g in plan.execution_goals if g.id == target.id), None)
            if fresh is None or fresh.tasks:
                # idempotency guard: a crash after commit (or a racing worker)
                # already populated this goal — never enrich twice
                self._finish_operation(uow, operation, PlanningOperationStatus.COMMITTED)
                return Signal.CONTINUE
            if cyclic:
                assert contract is not None
                fresh.contract = contract
                fresh.tasks = [
                    task.model_copy(update={"position": index}) for index, task in enumerate(tasks)
                ]
                try:
                    role_bindings = {
                        task.id: resolve_task_role_agents(
                            list(task.required_capabilities), self._agents
                        )
                        for task in fresh.tasks
                    }
                    for task in fresh.tasks:
                        task.role_agent_ids = role_bindings[task.id]
                        task.agent_id = task.role_agent_ids["implementer"]
                except ValueError as exc:
                    block = PlanBlock(
                        id=new_id(),
                        kind="agent_capability",
                        explanation=str(exc),
                        stage="goal_enrichment",
                        goal_id=fresh.id,
                        legal_resolutions=["retry_stage", "start_replan"],
                        created_at=self._clock.now(),
                    )
                    plan.open_block(block)
                    plan.bump_version()
                    uow.outbox.add(
                        PlanBlocked(
                            plan_id=plan_id,
                            block_id=block.id,
                            stage=block.stage,
                            goal_id=fresh.id,
                        )
                    )
                    uow.plans.save(plan)
                    self._finish_operation(
                        uow,
                        operation,
                        PlanningOperationStatus.FAILED,
                        failure_kind="agent_capability",
                        safe_message=str(exc),
                    )
                    return Signal.PAUSED
                plan._set_phase(PlanPhase.RUNNING)
            else:
                goals = [g.model_copy(deep=True) for g in plan.goals if not g.is_terminal]
                for goal in goals:
                    if goal.id == target.id:
                        goal.tasks = [
                            task.model_copy(update={"position": index})
                            for index, task in enumerate(tasks)
                        ]
                plan.set_iteration_goals(goals)
            plan.clear_planning_retry()  # progressed: disarm any prior backoff gate
            plan.bump_version()
            self._finish_operation(uow, operation, PlanningOperationStatus.COMMITTED)
            uow.plans.save(plan)
        return Signal.CONTINUE

    def _is_terminal_reasoner_failure(
        self,
        exc: ReasonerUnavailable,
        plan: Plan,
        next_attempt: int,
        operation: PlanningOperation | None,
    ) -> bool:
        """Whether this failure should escalate to a human-gated block.

        Gating on `exc.transient` alone was wrong in both directions. It escalated
        provider capacity after three attempts even though waiting is exactly what
        resolves it; and it treated "the model replied with prose instead of calling
        the submission tool" as equally worth retrying, when no amount of waiting
        makes an incapable model succeed.

        So: a PERMANENT failure is terminal immediately (unchanged). A CAPACITY kind
        keeps backing off until the outage outlives the wall-clock ceiling — the same
        bound execution uses. Every other transient kind keeps the ordinary attempt
        budget.
        """
        if not exc.transient:
            return True
        if exc.kind is not None and exc.kind in CAPACITY_KINDS and operation is not None:
            # The outage START. A wall-clock ceiling needs one, and the Plan
            # aggregate has no first-arm timestamp (only planning_retry_not_before
            # and planning_attempts), so back-computing it from the attempt count
            # and the backoff curve would be guesswork.
            #
            # The operation row is it: `find_active_planning_operation` REUSES one
            # row for the same purpose+target across the whole outage, and
            # `_start_operation` preserves `created_at` when it reuses it — only
            # status/updated_at/liveness are reset. A COMMITTED operation ends the
            # run, so the next failure gets a fresh row and a fresh clock.
            #
            # (Scanning the ledger for BACKING_OFF rows does NOT work: the reused
            # row is flipped back to STARTED at the top of every tick, so by the
            # time this runs the earlier BACKING_OFF is no longer visible.)
            return self._capacity.outage_exceeded(operation.created_at, self._clock.now(), None)
        # No operation means the quarantined LEGACY path, which records none: keep
        # it bounded by its original attempt budget rather than waiting forever.
        return next_attempt >= plan.retry_policy.max_attempts

    def _handle_reasoner_failure(
        self,
        plan_id: str,
        exc: ReasonerUnavailable,
        uow: UnitOfWork,
        operation: PlanningOperation | None = None,
    ) -> Signal:
        """A reasoner failure during ENRICHING: re-read + re-guard, then either arm
        the plan-level backoff gate (transient, budget left) or fail the plan
        (permanent, or budget exhausted). Emits a ReasonerFailed event either way so
        the frontend sees it; the transient path returns NOT_READY so the worker
        releases and sleeps (the gate blocks re-claim until it opens)."""
        with uow:
            plan = uow.plans.get(plan_id)
            cyclic = plan.status == PlanStatus.RUNNING and (
                plan.active_cycle is not None
                or (
                    plan.intent_proposal is not None
                    and plan.intent_proposal.approved_at is not None
                )
            )
            if (not cyclic and plan.phase not in WORKER_PLANNING_PHASES) or plan.paused:
                if operation is not None:
                    self._finish_operation(
                        uow,
                        operation,
                        PlanningOperationStatus.FAILED,
                        failure_kind="planning_interrupted",
                        safe_message="Planning stopped because the plan state changed.",
                    )
                return Signal.PAUSED  # raced by a human command; theirs wins
            phase = plan.activity if cyclic else plan.phase.value
            next_attempt = plan.planning_attempts + 1
            terminal = self._is_terminal_reasoner_failure(exc, plan, next_attempt, operation)

            if terminal:
                if cyclic:
                    # Scope the block to the goal being enriched when there is
                    # one (domain unfreeze #14). Enrichment is per-goal by
                    # construction and the operation already carries the target,
                    # so a single goal's reasoner failure must not freeze
                    # independently-running siblings. A plan-wide failure
                    # (cycle architecture) has target_goal_id=None and keeps the
                    # scalar block, exactly as before.
                    target_goal_id = operation.target_goal_id if operation is not None else None
                    if target_goal_id is not None and not any(
                        candidate.id == target_goal_id for candidate in plan.execution_goals
                    ):
                        target_goal_id = None
                    block = PlanBlock(
                        id=new_id(),
                        kind="reasoner_failure",
                        explanation=exc.reason,
                        stage=phase,
                        goal_id=target_goal_id,
                        legal_resolutions=["retry_stage", "start_replan"],
                        created_at=self._clock.now(),
                    )
                    plan.open_block(block)
                    uow.outbox.add(
                        PlanBlocked(
                            plan_id=plan_id,
                            block_id=block.id,
                            stage=block.stage,
                            goal_id=block.goal_id,
                        )
                    )
                else:
                    plan.fail_plan()
                plan.bump_version()
                uow.outbox.add(
                    ReasonerFailed(
                        plan_id=plan_id,
                        phase=phase,
                        reason=exc.reason,
                        transient=False,
                        retry_at=None,
                    )
                )
                if not cyclic:
                    uow.outbox.add(PlanFailed(plan_id=plan_id, reason=exc.reason))
                if operation is not None:
                    self._finish_operation(
                        uow,
                        operation,
                        PlanningOperationStatus.FAILED,
                        failure_kind="reasoner_failure",
                        safe_message=exc.reason,
                    )
                uow.plans.save(plan)
                return Signal.FAILED

            # Pass the REAL kind: `kind=None` capped every planning backoff at
            # max_backoff_seconds (900s) and skipped the per-kind patient curve, so
            # a rate-limited provider was re-polled every 15 minutes forever.
            delay = plan.retry_policy.backoff_for(next_attempt + 1, kind=exc.kind)
            if exc.retry_after_seconds is not None:
                delay = max(delay, exc.retry_after_seconds)  # provider knows better
            not_before = self._clock.now() + timedelta(seconds=delay) if delay > 0 else None
            plan.record_planning_retry(not_before)
            plan.bump_version()
            uow.outbox.add(
                ReasonerFailed(
                    plan_id=plan_id,
                    phase=phase,
                    reason=exc.reason,
                    transient=True,
                    retry_at=not_before.isoformat() if not_before else None,
                )
            )
            if operation is not None:
                self._finish_operation(
                    uow,
                    operation,
                    PlanningOperationStatus.BACKING_OFF,
                    failure_kind="reasoner_failure",
                    safe_message=exc.reason,
                    retry_at=not_before,
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
