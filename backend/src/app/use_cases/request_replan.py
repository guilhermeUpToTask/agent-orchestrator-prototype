"""request_replan — enter the conversational re-plan (state machinery only).

Two entry points, one phase: from REVIEW ("replan next phase") and from
mid-RUNNING user chat ("give me a new plan"). Either way the aggregate skips the
current iteration's PENDING work now (an in-flight task finalizes via the
tolerant finalize in ExecutionHandler) and the plan lands in REPLANNING — a
conversational phase that is NOT worker-claimable; each user message advances it
via the conversation use cases (roadmap Phase 2.5 wires the reasoning content).

This is distinct from apply_edit: apply_edit is the surgical manual edit;
request_replan is the holistic conversational re-plan.
"""

from __future__ import annotations

from src.domain.events.outbox import BlockResolved, ReplanRequested
from src.domain.value_objects.lifecycle import Status

from src.app.ports import UnitOfWork


def request_replan(plan_id: str, uow: UnitOfWork) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        from_phase = plan.phase.value

        # Resolve any active block FIRST (unfreeze #10): resolve_block's generic
        # fallback sets status=PAUSED, so begin_replanning() must run AFTER it to
        # make the coherent WAITING replan tuple the transaction's final lifecycle
        # word. The whole thing commits atomically, so no PAUSED state is ever
        # externally visible.
        block = plan.block
        if block is not None and block.active:
            block_id = block.id
            plan.resolve_block("start_replan", block.created_at)
            uow.outbox.add(
                BlockResolved(
                    plan_id=plan_id,
                    block_id=block_id,
                    resolution="start_replan",
                )
            )

        plan.begin_replanning()

        cycle = plan.active_cycle
        if cycle is not None:
            for goal in cycle.goals:
                for task in goal.tasks:
                    if task.status == Status.FAILED:
                        # Requeue is the existing guarded transition that makes
                        # a failed task eligible for the tolerant abandon path.
                        plan.requeue_task(goal.id, task.id)
                    if task.status in {Status.PENDING, Status.RUNNING}:
                        plan.abandon_execution_task(cycle.id, goal.id, task.id)

        plan.bump_version()
        uow.outbox.add(ReplanRequested(plan_id=plan_id, from_phase=from_phase))
        uow.plans.save(plan)
