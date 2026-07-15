"""Create or return the one long-lived ProjectPlan for a project."""

from __future__ import annotations

from src.app.ports import UnitOfWork
from src.domain.factories.plan_factory import PlanFactory
from src.domain.policies.retry_policies import RetryPolicy


def create_plan(
    brief: str,
    project_id: str,
    request_id: str,
    uow: UnitOfWork,
    retry_policy: RetryPolicy | None = None,
) -> str:
    with uow:
        existing = uow.plans.find_by_request_id(request_id)
        if existing is not None:
            return existing

        project_plan = uow.plans.find_by_project_id(project_id)
        if project_plan is not None:
            uow.plans.bind_request_id(request_id, project_plan)
            return project_plan

        plan = PlanFactory.create(brief, project_id, retry_policy)
        uow.plans.save(plan)
        uow.plans.bind_request_id(request_id, plan.id)
        return plan.id
