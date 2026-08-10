"""Closing a goal: reserve it, merge it into the cycle branch, observe the result.

Extracted from `ExecutionHandler` (P8.7 task 3, step 3). One concern with a
strict order — reserve, merge, re-guard, record, observe — and a matching set of
failure exits:

- **Reserve** (`reserve`) refuses a goal that cannot merge, which the caller
  turns into a block via `block_unpromotable` rather than letting it escape to
  the worker loop and hot-loop the same failure every tick.
- **Merge** (`promote`) is the only place a goal branch reaches the cycle
  branch. It re-guards the reservation, the cycle identity and the evidence
  INSIDE the finalize transaction, because the merge itself happened outside
  one. An environmental merge failure is re-attempted a bounded number of times
  (`_retry`); a conflict never is.
- **Observe** (`run_acceptance`, `pending_acceptance_cycle`) boots the assembled
  tree. ADVISORY: it is recorded beside the cycle and gates nothing, and every
  failure mode in it is swallowed on purpose.

Acceptance travels with promotion rather than standing alone because the two
points where a cycle branch changes meaning are a goal merge and the moment
before the publication gate opens — the first is `promote`'s own tail, and the
second is the ordering rule `pending_acceptance_cycle` exists to state.

`_flush_pending_artifacts` stays with the handler: `_retry` writes its artifact
after its plan transaction has closed, so it needs no queue.
"""

from __future__ import annotations

import structlog

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import PlanBlock
from agent_orchestrator.domain.events.outbox import GoalCompleted, PlanBlocked
from agent_orchestrator.domain.factories.identity import new_id
from agent_orchestrator.domain.services.lookups import find_goal
from agent_orchestrator.domain.services.navigation import can_promote_goal
from agent_orchestrator.domain.value_objects.lifecycle import Status

from agent_orchestrator.app.acceptance_records import AcceptanceRun
from agent_orchestrator.app.block_policy import resolutions_for
from agent_orchestrator.app.branch_names import cycle_branch, goal_branch
from agent_orchestrator.app.environment_port import AcceptanceTrigger, EnvironmentSpec
from agent_orchestrator.app.environment_port import ProjectEnvironment
from agent_orchestrator.app.handlers.base import Signal
from agent_orchestrator.app.handlers.execution_rules import orchestration_failure
from agent_orchestrator.app.ports import (
    Clock,
    PlanningArtifact,
    PlanningArtifactStore,
    TaskFailed,
    UnitOfWork,
    Workspace,
)
from agent_orchestrator.app.promotion_failures import is_transient_merge_failure
from agent_orchestrator.app.promotion_records import GoalPromotion

from pathlib import Path
from typing import Callable

log = structlog.get_logger(__name__)

# Re-attempts of a goal->cycle merge that failed for an ENVIRONMENTAL reason. The
# merge is cheap; if two re-attempts do not clear it, the condition is not
# momentary and a human should see it.
MAX_PROMOTION_RETRIES = 2


class GoalPromoter:
    """Reserves, merges and observes one goal's promotion into its cycle branch."""

    def __init__(
        self,
        workspace: Workspace,
        clock: Clock,
        planning_artifacts: PlanningArtifactStore | None = None,
        environment: ProjectEnvironment | None = None,
        environment_context: Callable[[str], tuple[Path, EnvironmentSpec | None]] | None = None,
    ) -> None:
        self._workspace = workspace
        self._clock = clock
        # Without a durable place to count its own attempts, a transient merge
        # failure simply blocks exactly as it did before the retry existed.
        self._planning_artifacts = planning_artifacts
        # The cycle acceptance run (P8.2). Optional: without an adapter the
        # orchestrator behaves exactly as it did, which is why NoEnvironment is
        # the permanent fallback rather than a placeholder. The verdict is
        # ADVISORY — it is recorded beside the cycle and gates nothing.
        self._environment = environment
        self._environment_context = environment_context

    def block_unpromotable(
        self, plan_id: str, plan: Plan, goal: Goal, failure: TaskFailed, uow: UnitOfWork
    ) -> Signal:
        """A goal navigation selected to close but that cannot merge (a task is
        not DONE or has no accepted evidence — typically a legacy/replan artifact)
        opens a structured block, mirroring `_pause_on_failed_goal`. Without this
        the reservation's TaskFailed escapes `handle()` to the worker loop, which
        re-dispatches and re-raises it every tick (a 1Hz poisoned-plan storm)."""
        offending = next(
            task
            for task in goal.tasks
            if task.status != Status.DONE or not task.verification_evidence
        )
        # Only advertise resolutions that can actually repair this block. `retry`
        # requires a FAILED task (Task.retry rejects skipped/cancelled/terminal ->
        # pending), so a goal wedged by a SKIPPED/evidence-less-DONE task is
        # recoverable only via edit_task or start_replan — offering retry_stage
        # there is a nominal-only resolution that 422s when the operator tries it.
        #
        # Domain unfreeze #14: each goal opens its OWN block now
        # (`Plan.goal_blocks[goal.id]`) -- a different goal's block never
        # collides here (separate dict entries), and `claim_ready_goal`'s
        # candidate scan excludes any goal with an active block from ever
        # being re-selected, so this goal's own block can't collide with
        # itself on a later tick either. No pre-check needed; open_block's
        # "already active" guard is a genuine-bug detector now, not routine.
        resolutions = resolutions_for(
            "execution_failure", task_retryable=offending.status == Status.FAILED
        )
        block = PlanBlock(
            id=new_id(),
            kind="execution_failure",
            explanation=str(failure),
            stage=offending.tdd_stage,
            goal_id=goal.id,
            task_id=offending.id,
            task_revision=offending.revision,
            legal_resolutions=resolutions,
            created_at=self._clock.now(),
        )
        plan.open_block(block)
        uow.outbox.add(
            PlanBlocked(
                plan_id=plan_id,
                block_id=block.id,
                stage=block.stage,
                goal_id=goal.id,
                task_id=offending.id,
                task_revision=offending.revision,
            )
        )
        plan.bump_version()
        uow.plans.save(plan)
        return Signal.PAUSED

    def reserve(
        self,
        plan: Plan,
        goal: Goal,
        uow: UnitOfWork,
    ) -> tuple[str, str, str]:
        cycle = plan.active_cycle
        assert cycle is not None
        if not can_promote_goal(goal):
            raise TaskFailed(
                "goal cannot merge without accepted task evidence",
                # Class C (a promotion-time state check, not the agent's work). Un-freeze #17 made VERIFICATION_ERROR
                # retryable for candidate rejections; this is not one, so it
                # keeps the independent `retryable` veto.
                failure=orchestration_failure("goal cannot merge without accepted task evidence"),
            )
        reservation = f"goal:{cycle.id}:{goal.id}"
        plan.reserve_promotion(goal.id, reservation)
        plan.bump_version()
        uow.plans.save(plan)
        return reservation, cycle.id, goal.id

    def pending_acceptance_cycle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> str | None:
        """The cycle owing a pre-publication acceptance run, or None.

        WHY THIS RUNS BEFORE THE GATE OPENS, and why that removes any reason to
        put the acceptance run in the domain:

        * `Plan.activity` checks `review_gate` BEFORE it falls through to
          `cycle_verification`, so a run started after the gate opened reports
          `review:cycle_completion` and leaves `cycle_verification` naming an
          empty slot forever. Run it here and the existing DERIVATION produces
          `cycle_verification` on its own — no new field, no un-freeze.
        * A gate open while the run is still executing is a race: booting an
          application takes minutes, and an operator (or a fixture script) can
          record a disposition before the verdict they were meant to read
          exists. Opening the gate only once a verdict is recorded closes it.

        Idempotency lives in the ledger rather than in plan state: one
        `pre_publication` row per cycle means done. A worker that dies mid-run
        leaves no row, so the next tick simply runs it again — which is the
        correct behaviour for a fresh observation, and needs nothing persisted
        in the aggregate.
        """
        if self._environment is None or self._environment_context is None:
            return None
        cycle = plan.active_cycle
        if cycle is None:
            return None
        already = any(
            run.trigger == "pre_publication"
            for run in uow.acceptance_runs.list_for_cycle(plan_id, cycle.id)
        )
        return None if already else cycle.id

    def run_acceptance(
        self,
        plan_id: str,
        cycle_id: str,
        goal_id: str | None,
        trigger: AcceptanceTrigger,
        uow: UnitOfWork,
    ) -> None:
        """Boot the assembled tree and record what happened. ADVISORY ONLY.

        Called at the two points where the cycle branch has just changed
        meaning: after a goal merges (early signal, so a broken application is
        found at goal 2 rather than at publication) and before the publication
        gate opens (the verdict an operator actually decides on).

        Every failure mode here is swallowed on purpose. An acceptance run
        observes; it must never be able to fail a promotion that passed
        verification, or stop a publication gate from opening. `verify()` is
        already contracted not to raise — this is the belt to that adapter's
        braces, because a third-party adapter is exactly the thing that will.
        """
        if self._environment is None or self._environment_context is None:
            return
        ref = cycle_branch(cycle_id)
        try:
            repo, spec = self._environment_context(plan_id)
            verdict = self._environment.verify(repo, ref, spec)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("acceptance.adapter_raised", plan_id=plan_id, error=str(exc))
            return
        if not verdict.is_signal:
            # Nothing configured. Recording a row per goal merge saying "nobody
            # asked" is noise, not evidence.
            return
        try:
            with uow:
                uow.acceptance_runs.add(
                    AcceptanceRun(
                        id=new_id(),
                        plan_id=plan_id,
                        cycle_id=cycle_id,
                        goal_id=goal_id,
                        trigger=trigger,
                        ref=ref,
                        outcome=verdict.outcome,
                        summary=verdict.summary,
                        detail=verdict.detail,
                        duration_seconds=verdict.duration_seconds,
                        created_at=self._clock.now(),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("acceptance.record_failed", plan_id=plan_id, error=str(exc))
            return
        log.info(
            "acceptance.recorded",
            plan_id=plan_id,
            cycle_id=cycle_id,
            trigger=trigger,
            outcome=verdict.outcome,
        )

    def _retry(
        self,
        plan_id: str,
        goal_id: str,
        reservation: str,
        exc: Exception,
        uow: UnitOfWork,
    ) -> bool:
        """Release the reservation and let a later tick re-attempt the merge.

        Only for environmental failures, and only a bounded number of times. The
        reservation MUST be released either way — holding it would block the very
        retry this exists to allow, and `assert_lifecycle_mutation_allowed`
        refuses every edit while one is open.

        Bounded by attempts rather than wall clock: the merge is cheap, so if two
        re-attempts do not clear it the condition is not momentary, and a human
        should see it.
        """
        if self._planning_artifacts is None:
            return False  # nowhere to count, so no unbounded loop is possible
        if not is_transient_merge_failure(str(exc)):
            return False

        prior = self._planning_artifacts.latest(
            plan_id, "goal_promotion_retry", goal_id=goal_id, limit=MAX_PROMOTION_RETRIES + 1
        )
        if len(prior) >= MAX_PROMOTION_RETRIES:
            return False

        with uow:
            plan = uow.plans.get(plan_id)
            if plan.goal_promotion_reservations.get(goal_id) != reservation:
                return False  # someone else owns it now; do not touch their state
            plan.release_promotion(goal_id, reservation)
            plan.bump_version()
            uow.plans.save(plan)

        self._planning_artifacts.append(
            PlanningArtifact(
                plan_id=plan_id,
                goal_id=goal_id,
                purpose="goal_promotion_retry",
                sequence=0,
                input_fingerprint=goal_id,
                outcome="abandoned",
                rejection_reasons=(str(exc)[:500],),
                created_at=self._clock.now(),
            )
        )
        log.info("promotion.retrying", plan_id=plan_id, goal_id=goal_id, error=str(exc)[:200])
        return True

    async def promote(
        self,
        plan_id: str,
        promotion: tuple[str, str, str],
        uow: UnitOfWork,
    ) -> Signal:
        reservation, cycle_id, goal_id = promotion
        try:
            commit_sha = await self._workspace.merge_goal(plan_id, cycle_id, goal_id)
        except Exception as exc:
            # A verified goal must not be thrown away because the repository was
            # momentarily unusable. A stale worktree registration or a held index
            # lock clears on its own; a CONFLICT never does.
            if self._retry(plan_id, goal_id, reservation, exc, uow):
                return Signal.NOT_READY
            with uow:
                plan = uow.plans.get(plan_id)
                if plan.goal_promotion_reservations.get(goal_id) != reservation:
                    return Signal.PAUSED
                plan.release_promotion(goal_id, reservation)
                block = PlanBlock(
                    id=new_id(),
                    kind="goal_promotion_failure",
                    explanation=f"goal Git promotion failed: {exc}",
                    stage="merge",
                    goal_id=goal_id,
                    legal_resolutions=resolutions_for("goal_promotion_failure"),
                    created_at=self._clock.now(),
                )
                plan.open_block(block)
                plan.bump_version()
                uow.outbox.add(
                    PlanBlocked(
                        plan_id=plan_id,
                        block_id=block.id,
                        stage=block.stage,
                        goal_id=goal_id,
                    )
                )
                uow.plans.save(plan)
            return Signal.PAUSED

        with uow:
            plan = uow.plans.get(plan_id)
            if plan.goal_promotion_reservations.get(goal_id) != reservation:
                return Signal.PAUSED
            cycle = next(
                (item for item in plan.cycles if item.id == cycle_id),
                None,
            )
            if cycle is None or plan.active_cycle is None or plan.active_cycle.id != cycle_id:
                raise TaskFailed(
                    "goal promotion targets a superseded cycle",
                    # Class C (a promotion race; the agent has nothing to repair). Un-freeze #17 made VERIFICATION_ERROR
                    # retryable for candidate rejections; this is not one, so it
                    # keeps the independent `retryable` veto.
                    failure=orchestration_failure("goal promotion targets a superseded cycle"),
                )
            goal = find_goal(cycle.goals, goal_id)
            if any(
                task.status != Status.DONE or not task.verification_evidence for task in goal.tasks
            ):
                raise TaskFailed(
                    "goal evidence changed during promotion",
                    # Class C (a promotion race; the agent has nothing to repair). Un-freeze #17 made VERIFICATION_ERROR
                    # retryable for candidate rejections; this is not one, so it
                    # keeps the independent `retryable` veto.
                    failure=orchestration_failure("goal evidence changed during promotion"),
                )
            cycle.evidence_refs.append(f"git:{commit_sha}")
            # Recorded HERE, not at the merge call: everything above this line
            # in the transaction has already re-guarded the promotion
            # reservation, so a promotion that lost its reservation returned
            # PAUSED without leaving a phantom row. The refs come from the same
            # module the workspace adapter builds its branches from.
            uow.promotions.add(
                GoalPromotion(
                    id=new_id(),
                    plan_id=plan_id,
                    cycle_id=cycle_id,
                    goal_id=goal_id,
                    from_ref=goal_branch(goal_id),
                    into_ref=cycle_branch(cycle_id),
                    merge_sha=commit_sha,
                    promoted_at=self._clock.now(),
                )
            )
            plan.complete_goal(goal_id)
            plan.release_promotion(goal_id, reservation)
            plan.bump_version()
            uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal_id))
            uow.plans.save(plan)
        # AFTER the transaction closes: booting an application is a side effect,
        # and invariant #5 keeps those out of transactions. Advisory, so a
        # verdict of any kind leaves the promotion above untouched.
        self.run_acceptance(plan_id, cycle_id, goal_id, "goal_merge", uow)
        return Signal.CONTINUE
