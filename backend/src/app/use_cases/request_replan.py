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

from src.domain.errors.planning_errors import InvalidEditError
from src.domain.events.outbox import BlockResolved, ReplanRequested

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

        # Domain unfreeze #13: a replan is a holistic, plan-wide mutation --
        # resolve EVERY active per-goal block too (not just the legacy scalar
        # above), since the source cycle they belong to is about to be frozen
        # and superseded anyway (activate_cycle wipes goal_blocks wholesale
        # once the replacement cycle lands; resolving them now keeps
        # status_reason/legal_actions/activity coherent for the WAITING
        # window in between, rather than reporting a stale "partially
        # blocked" fact during an active replan conversation). "start_replan"
        # is expected in every execution-triggered block's legal_resolutions,
        # but that is an invariant of the block-opening call sites, not of
        # this use case -- verify it explicitly rather than let a mismatch
        # silently skip a goal (a stale block with a bad resolution set
        # would otherwise leave that goal permanently, invisibly stuck).
        for goal_id, goal_block in list(plan.goal_blocks.items()):
            if not goal_block.active:
                continue
            if "start_replan" not in goal_block.legal_resolutions:
                raise InvalidEditError(
                    f"goal '{goal_id}' has an active block at stage "
                    f"'{goal_block.stage}' that cannot resolve via 'start_replan' "
                    f"(legal resolutions: {goal_block.legal_resolutions})"
                )
            block_id = goal_block.id
            plan.resolve_block("start_replan", goal_block.created_at, goal_id=goal_id)
            uow.outbox.add(
                BlockResolved(
                    plan_id=plan_id,
                    block_id=block_id,
                    resolution="start_replan",
                )
            )

        plan.begin_replanning()

        # unfreeze #11: do NOT rewrite the still-active source cycle's task
        # outcomes. SKIPPED is legacy iteration-abandonment residue — invalid for
        # an active cyclic goal (it makes the goal permanently unpromotable:
        # navigation treats {DONE,SKIPPED} as closeable but promotion requires
        # every task DONE-with-evidence). Replanning revokes claimability via
        # status=WAITING (unfreeze #10); the source cycle stays frozen and is
        # superseded only when the replacement cycle activates. Late worker
        # results settle in the execution ledger without changing task outcomes.

        plan.bump_version()
        uow.outbox.add(ReplanRequested(plan_id=plan_id, from_phase=from_phase))
        uow.plans.save(plan)
