"""claim_ready_goal — the goal-level claim scan (ADR-001, domain unfreeze #12).

`GoalLeaseRepository.claim_one_ready_goal` (src/domain/repositories/goal_lease_repo.py)
is a pure claim primitive over ONE already-identified `(plan_id, goal_id)`
pair — it does no scanning. "Which goal is ready" is derived from the Plan
aggregate's JSON document (`navigation.ready_goal_ids`), not a SQL column, so
the scan itself lives here in the use-case layer: a cheap indexed candidate
list (`PlanRepository.list_running_ids`, oldest-updated-first so one busy
plan can't starve the rest) reconstructed one at a time, checked for a ready
AND already-enriched goal, then an indexed claim attempt — moving to the next
candidate on a lost race.

`scan_limit` bounds worst-case work per call; it is NOT derived from load
testing yet (ROADMAP-flagged: tune empirically once real contention is
observable, don't guess further).
"""

from __future__ import annotations

from src.app.ports import Clock, UnitOfWork
from src.domain.services.navigation import ready_goal_ids


def claim_ready_goal(
    uow: UnitOfWork,
    worker_id: str,
    lease_seconds: int,
    clock: Clock,
    scan_limit: int = 20,
) -> tuple[str, str] | None:
    """Return the `(plan_id, goal_id)` this worker now holds the lease for,
    or None if no ready-and-unenriched goal was claimable this scan."""
    now = clock.now()
    with uow:
        candidate_ids = uow.plans.list_running_ids(scan_limit)

    for plan_id in candidate_ids:
        with uow:
            plan = uow.plans.get(plan_id)
        if plan.active_cycle is None or plan.paused or plan.pause_requested:
            continue
        ready_ids = ready_goal_ids(plan.execution_goals, now)
        enriched_ready_goal_ids = [
            goal.id for goal in plan.execution_goals if goal.id in ready_ids and goal.tasks
        ]
        for goal_id in enriched_ready_goal_ids:
            # goal_leases manages its own transaction per call (unlike
            # plans/executions/outbox, it is never bound to `with uow:`) --
            # calling it standalone here is correct, not an oversight.
            if uow.goal_leases.claim_one_ready_goal(plan_id, goal_id, worker_id, lease_seconds, now):
                return plan_id, goal_id
    return None
