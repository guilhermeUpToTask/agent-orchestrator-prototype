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

from src.domain.events.outbox import ReplanRequested

from src.app.ports import UnitOfWork


def request_replan(plan_id: str, uow: UnitOfWork) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        from_phase = plan.phase.value
        plan.begin_replanning()
        plan.bump_version()
        uow.outbox.add(ReplanRequested(plan_id=plan_id, from_phase=from_phase))
        uow.plans.save(plan)
