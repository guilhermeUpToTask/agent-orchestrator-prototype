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
    ) -> None:
        """Renew the pair's lease only while it is still owned by the worker."""
        ...

    def release(self, plan_id: str, goal_id: str, worker_id: str) -> None:
        """Release the pair only while it is still owned by the worker."""
        ...

    def is_claim_live(self, plan_id: str, goal_id: str, now: datetime) -> bool:
        """Report whether the pair currently has an unexpired worker claim."""
        ...
