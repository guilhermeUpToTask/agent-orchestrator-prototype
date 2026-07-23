"""Operator-tunable plan-level retry/backoff budget (un-freeze #12).

Distinct from `container.default_retry_policy` (infra/policies/retry_policy_factory.py),
which only seeds a NEW plan at creation: this mutates the policy already
persisted on an EXISTING plan, so an operator can widen the backoff budget for
a plan currently stuck on a transient/rate-limited failure without a replan.
"""

from __future__ import annotations

from typing import Any

from src.app.ports import UnitOfWork


def update_retry_policy(plan_id: str, updates: dict[str, Any], uow: UnitOfWork) -> None:
    """`updates` carries only the fields the operator actually set (partial
    merge over the plan's current policy) — untouched fields keep their
    current value rather than resetting to the domain's bare defaults."""
    with uow:
        plan = uow.plans.get(plan_id)
        merged = plan.retry_policy.model_copy(update=updates)
        plan.update_retry_policy(merged)
        plan.bump_version()
        uow.plans.save(plan)
