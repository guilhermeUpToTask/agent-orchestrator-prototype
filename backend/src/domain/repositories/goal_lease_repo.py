from __future__ import annotations

from datetime import datetime
from typing import Protocol


class GoalLeaseRepository(Protocol):
    """Per-goal concurrency primitives for one already-selected ready goal.

    The application identifies ready goals from the Plan aggregate; this contract
    only coordinates ownership of one ``(plan_id, goal_id)`` pair at a time.
    """

    def claim_one_ready_goal(
        self,
        plan_id: str,
        goal_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> bool:
        """Claim the pair if it is unclaimed or its existing lease has expired."""
        ...

    def heartbeat(
        self,
        plan_id: str,
        goal_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> bool:
        """Renew an unexpired lease owned by the worker and report success."""
        ...

    def release(self, plan_id: str, goal_id: str, worker_id: str) -> None:
        """Release the pair only while it is still owned by the worker."""
        ...

    def is_claim_live(self, plan_id: str, goal_id: str, now: datetime) -> bool:
        """Report whether the pair currently has an unexpired worker claim."""
        ...

    def lease_holder(self, plan_id: str, goal_id: str) -> tuple[str, datetime] | None:
        """`(worker_id, lease_expires_at)` for this goal's claim, expired or not.

        The goal lease is the one that matters for execution: a goal worker holds
        it, not the plan claim. Returned even when expired, because "expired" is
        exactly the fact an operator is missing when a goal reports RUNNING and
        nothing is happening.
        """
        ...
