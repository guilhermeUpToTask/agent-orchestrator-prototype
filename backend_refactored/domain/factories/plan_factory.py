"""Factory for the Plan aggregate. Pure construction — no I/O, no repo. The
repository CALLS this to reconstruct; this never calls the repository.

Two methods for two scenarios:
- create():      build from zero (user starts a new plan). Runs birth invariants
                 (a plan must have a brief), generates id, applies defaults.
- reconstruct(): rebuild from persisted state (repo loading a row). Trusts the
                 stored data was valid when saved; does NOT regenerate id or
                 re-apply defaults over real values.
"""
from __future__ import annotations

from typing import Any

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.errors.planning_errors import EmptyPlanError
from domain.factories.identity import new_id
from domain.policies.retry_policies import RetryPolicy


class PlanFactory:
    @staticmethod
    def create(brief: str, retry_policy: RetryPolicy | None = None) -> Plan:
        if not brief or not brief.strip():
            raise EmptyPlanError("brief is required")
        return Plan(
            id=new_id(),
            version=0,
            brief=brief.strip(),
            phase=PlanPhase.DISCOVERY,
            iteration=1,
            retry_policy=retry_policy or RetryPolicy(),
            goals=[],
        )

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> Plan:
        """Rebuild from a persisted representation (e.g. assembled from SQLite
        rows). The aggregate validates field types via Pydantic; this method's
        job is to trust-and-rehydrate, not to re-run creation logic."""
        return Plan.model_validate(data)
