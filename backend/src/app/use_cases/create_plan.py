"""Create or return the one long-lived ProjectPlan for a project."""

from __future__ import annotations

from dataclasses import dataclass

from src.app.ports import UnitOfWork
from src.domain.factories.plan_factory import PlanFactory
from src.domain.policies.retry_policies import RetryPolicy


@dataclass(frozen=True)
class OpenProjectPlanResult:
    plan_id: str
    created: bool
    request_replayed: bool


def open_project_plan(
    brief: str,
    project_id: str,
    request_id: str,
    uow: UnitOfWork,
    retry_policy: RetryPolicy | None = None,
) -> OpenProjectPlanResult:
    with uow:
        existing = uow.plans.find_by_request_id(request_id)
        if existing is not None:
            return OpenProjectPlanResult(plan_id=existing, created=False, request_replayed=True)

        project_plan = uow.plans.find_by_project_id(project_id)
        if project_plan is not None:
            uow.plans.bind_request_id(request_id, project_plan)
            return OpenProjectPlanResult(
                plan_id=project_plan, created=False, request_replayed=False
            )

        plan = PlanFactory.create(brief, project_id, retry_policy)
        uow.plans.save(plan)
        uow.plans.bind_request_id(request_id, plan.id)
        return OpenProjectPlanResult(plan_id=plan.id, created=True, request_replayed=False)


def create_plan(
    brief: str,
    project_id: str,
    request_id: str,
    uow: UnitOfWork,
    retry_policy: RetryPolicy | None = None,
) -> str:
    """Compatibility wrapper for application callers that only need the id."""
    return open_project_plan(brief, project_id, request_id, uow, retry_policy).plan_id
