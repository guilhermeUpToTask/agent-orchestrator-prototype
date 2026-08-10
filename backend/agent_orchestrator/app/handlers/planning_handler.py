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

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from agent_orchestrator.domain.aggregates.planner_orchestrator import (
    Plan,
    PlanPhase,
    WORKER_PLANNING_PHASES,
)
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.errors.agent_errors import RoleUnsatisfiableError
from agent_orchestrator.domain.entities.planning_artifacts import (
    CycleDraft,
    PlanBlock,
    PlanStatus,
    ReviewGate,
    ReviewSubjectType,
)
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.events.outbox import (
    AgentFellBackToDefault,
    CycleDrafted,
    PhaseAdvanced,
    PlanBlocked,
    PlanFailed,
    ReasonerFailed,
    ReviewGateOpened,
)
from agent_orchestrator.domain.factories.identity import new_id
from agent_orchestrator.domain.repositories.agent_repo import AgentRepository
from agent_orchestrator.domain.repositories.capability_repo import CapabilityRepository
from agent_orchestrator.domain.services.agent_role_resolution import resolve_task_role_agents
from agent_orchestrator.domain.services.navigation import ready_goal_ids

from agent_orchestrator.app.provider_capacity import CAPACITY_KINDS, ProviderCapacityPolicy
from agent_orchestrator.app.handlers.base import Signal
from agent_orchestrator.app.block_policy import resolutions_for
from agent_orchestrator.app.execution_records import PlanningOperation, PlanningOperationStatus
import structlog

from agent_orchestrator.app.ports import (
    Clock,
    PlanningArtifact,
    PlanningArtifactStore,
    Reasoner,
    ReasonerUnavailable,
    UnitOfWork,
)

log = structlog.get_logger(__name__)


def _unenriched_ready_goals(plan: Plan, now: datetime) -> list[Goal]:
    """Every non-terminal, dependency-ready goal still without tasks, in
    position order.

    Dependency-readiness is the whole safety argument: a goal whose
    `depends_on` is unmet must never be enriched, because freezing its contract
    against a repository the goal it depends on has not written yet produces a
    contract nothing can satisfy. `ready_goal_ids` is the ONE rule that decides
    that — the same one the execution loop honours — and this must not grow a
    second (goal-parallelism fan-out, ADR-001).

    Returning the whole set rather than its first member is what makes
    enrichment parallel (P8.6 Task 2). It used to return `min(...)`, so a cycle
    with five independent goals paid five sequential reasoner sessions — ~25
    minutes of pure sequencing measured in the 2026-08-09 latency analysis —
    for work that has no ordering constraint between its members at all.
    """
    ready_ids = ready_goal_ids(plan.execution_goals, now)
    blocked_goal_ids = {goal_id for goal_id, block in plan.goal_blocks.items() if block.active}
    candidates = [
        goal
        for goal in plan.execution_goals
        if goal.id in ready_ids and goal.id not in blocked_goal_ids and not goal.tasks
    ]
    return sorted(candidates, key=lambda g: g.position)


class PlanningHandler:
    def __init__(
        self,
        reasoner: Reasoner,
        agents: AgentRepository,
        capabilities: CapabilityRepository,
        clock: Clock,
        capacity: ProviderCapacityPolicy | None = None,
        planning_artifacts: PlanningArtifactStore | None = None,
        max_concurrent_enrichment: int = 4,
    ) -> None:
        self._reasoner = reasoner
        self._agents = agents
        self._capabilities = capabilities
        self._clock = clock
        # Same ceilings execution uses, so planning and execution ride out a
        # provider outage for the same length of time instead of one of them
        # escalating first and blocking the plan the other was patiently waiting on.
        self._capacity = capacity or ProviderCapacityPolicy()
        # Written OUTSIDE the plan transaction (the ChatStore rule): work kept so
        # a retry can learn from it must survive the failure that produced it.
        self._planning_artifacts = planning_artifacts
        # How many ready goals may be enriched in one pass. Bounded rather than
        # unbounded because a fan-out of one provider session per ready goal is
        # how a cycle trips the very capacity limits P8.6 exists to stop waiting
        # on. The worker passes its own `max_concurrent_goals` so enrichment and
        # execution agree on how wide this process goes.
        self._max_concurrent_enrichment = max(1, max_concurrent_enrichment)

    def _record_planning_artifact(
        self,
        plan_id: str,
        goal_id: str | None,
        exc: ReasonerUnavailable,
        operation: PlanningOperation | None = None,
        purpose: str = "goal_contract",
    ) -> None:
        """Persist a failed attempt's work so the retry is better informed.

        Best-effort by design: memory is an optimisation, and losing it must
        never turn a recoverable planning failure into a worse one.
        """
        artifact = getattr(exc, "partial_artifact", None)
        reasons = tuple(getattr(exc, "rejection_reasons", ()) or ())
        fingerprint = getattr(exc, "input_fingerprint", None)
        if self._planning_artifacts is None or fingerprint is None:
            return
        # An attempt that produced NOTHING is still worth recording. The most
        # common enrichment failure is a session dying on its turn budget without
        # ever submitting — observed live — and if that leaves no row, the retry
        # is granted no extra turns and dies exactly the same way. The row buys
        # the escalating budget; the outcome filter keeps it out of the replay,
        # because there is no rejection to learn from.
        try:
            self._planning_artifacts.append(
                PlanningArtifact(
                    plan_id=plan_id,
                    goal_id=goal_id,
                    purpose=purpose,
                    # The operation row is REUSED across a whole outage, so this
                    # ties every attempt of one planning operation together while
                    # `sequence` keeps them ordered and distinct.
                    operation_id=operation.id if operation is not None else None,
                    sequence=0,  # the store assigns the next one
                    input_fingerprint=fingerprint,
                    outcome="rejected" if reasons else "abandoned",
                    payload=artifact,
                    rejection_reasons=reasons,
                    turns_used=getattr(exc, "turns_used", None),
                    created_at=self._clock.now(),
                )
            )
        except Exception as exc_write:  # noqa: BLE001
            log.warning("planning.artifact_write_failed", plan_id=plan_id, error=str(exc_write))

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
            # Architecture fails exactly the way enrichment does — a session that
            # burns its budget without submitting — and it is the FIRST stage a
            # starved reasoner hits, so leaving it without memory means the retry
            # restarts from nothing at the very point the plan gets stuck.
            self._record_planning_artifact(
                plan_id, None, exc, operation, purpose="cycle_architecture"
            )
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
        targets = _unenriched_ready_goals(plan, self._clock.now())
        if not targets:
            return await self._bind_and_gate(plan_id, uow)
        targets = targets[: self._max_concurrent_enrichment]
        if len(targets) == 1:
            return await self._enrich_one(plan_id, targets[0], plan, uow)

        # Concurrent, on ONE UnitOfWork, which is safe for exactly one reason
        # and it is worth stating: every `with uow:` block below contains no
        # `await`, so asyncio cannot interleave two transactions on the shared
        # connection — each commit runs to completion before another coroutine
        # is resumed. The only await is the reasoner session itself, held
        # outside any transaction (the side-effects-outside-transactions rule).
        # Each goal re-reads the plan inside its own commit, so the second
        # committer observes the first's version rather than clobbering it. If
        # anything here ever needs to await inside a transaction, this has to
        # become one UnitOfWork per goal instead.
        signals = await asyncio.gather(
            *(self._enrich_one(plan_id, target, plan, uow) for target in targets)
        )
        # Any goal that committed means the plan moved, so the loop ticks again
        # and picks up whatever this pass did not cover (goals past the bound,
        # and goals whose dependencies these just satisfied). Only when NOTHING
        # progressed does the pass inherit a peer's stop signal — otherwise one
        # rate-limited session would strand the goals that succeeded beside it.
        for signal in signals:
            if signal == Signal.CONTINUE:
                return Signal.CONTINUE
        return signals[0]

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
            # Record what the dead session had produced BEFORE handling the
            # failure, and outside any transaction: the point of the artifact is
            # to outlive this failure, so it must not ride the rollback.
            self._record_planning_artifact(plan_id, target.id, exc, operation)
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
                except (ValueError, RoleUnsatisfiableError) as exc:
                    # RoleUnsatisfiableError is a DomainError, NOT a ValueError.
                    # Role resolution used to raise a bare ValueError; giving it a
                    # stable `code` (so the API could map it) silently made this
                    # handler stop catching it, and an unsatisfiable binding
                    # crashed the worker on every tick instead of opening the
                    # agent_capability block that `retry_stage` exists to clear.
                    # Observed live: six identical tracebacks, one per poll.
                    block = PlanBlock(
                        id=new_id(),
                        kind="agent_capability",
                        explanation=str(exc),
                        stage="goal_enrichment",
                        goal_id=fresh.id,
                        legal_resolutions=resolutions_for("agent_capability"),
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
        # TOOL_ERROR deliberately does NOT get the wall-clock ceiling, even though
        # Phase 2 makes each planning retry strictly better informed. The kind
        # conflates two opposite situations: a HARD GOAL (a session that burned its
        # turns, where the escalating budget genuinely helps) and an INCAPABLE
        # MODEL (prose instead of a tool call, which no amount of waiting fixes).
        # Waiting out the second for hours before telling anyone is worse than
        # giving up on the first after a few attempts. Separating them needs a
        # distinct signal, not a longer clock.
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
                        legal_resolutions=resolutions_for("reasoner_failure"),
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
