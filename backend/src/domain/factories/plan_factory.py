"""Construction and reconstruction of the long-lived ProjectPlan."""

from __future__ import annotations

from typing import Any

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.planning_artifacts import PlanStatus
from src.domain.errors.planning_errors import EmptyPlanError
from src.domain.factories.identity import new_id
from src.domain.policies.retry_policies import RetryPolicy


class PlanFactory:
    @staticmethod
    def create(
        brief: str,
        project_id: str,
        retry_policy: RetryPolicy | None = None,
    ) -> Plan:
        if not brief or not brief.strip():
            raise EmptyPlanError("brief is required")
        if not project_id or not project_id.strip():
            raise EmptyPlanError("project_id is required")
        return Plan(
            id=new_id(),
            project_id=project_id,
            status=PlanStatus.WAITING,
            version=0,
            brief=brief.strip(),
            phase=PlanPhase.DISCOVERY,
            iteration=1,
            retry_policy=retry_policy or RetryPolicy(),
            goals=[],
        )

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> Plan:
        return Plan.model_validate(data)
