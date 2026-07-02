"""create_plan — entry point that turns a brief into a persisted Plan.

Idempotent on a client-supplied request_id (API-layer idempotency): a retried or
double-clicked create returns the SAME plan id instead of making a duplicate.
This is a DIFFERENT idempotency layer from task-execution idempotency (which is
check-before-act on task.result). Both are needed; they protect different things.
"""
from __future__ import annotations

from domain.factories.plan_factory import PlanFactory
from domain.policies.retry_policies import RetryPolicy

from application.ports import UnitOfWork


def create_plan(
    brief: str,
    request_id: str,
    uow: UnitOfWork,
    retry_policy: RetryPolicy | None = None,
) -> str:
    """Create a plan from a brief. Returns the plan id. Idempotent on request_id."""
    with uow:
        existing = uow.plans.find_by_request_id(request_id)
        if existing is not None:
            return existing  # already created for this request — return same id

        plan = PlanFactory.create(brief, retry_policy)  # birth invariant: brief required
        uow.plans.save(plan)
        uow.plans.bind_request_id(request_id, plan.id)
        return plan.id
