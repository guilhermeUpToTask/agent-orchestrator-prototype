"""claim_ready_goal — the goal-level claim scan (ADR-001, domain unfreeze #12;
symmetric-leases redesign, domain unfreeze #13).

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

Domain unfreeze #13 removed the "privileged plan-level goal" this scan used
to have to exclude (a bug found live last session: the plan-level tick always
drove the position-earliest goal directly via `next_action`, regardless of
its own readiness, and this scan had to carve that one goal out of its
candidate set to avoid a genuine double-dispatch race with it — see git
history for that fix's full account). `advance_plan.py`'s cyclic branch no
longer dispatches execution at all; EVERY ready+enriched goal, including
position 0, is now claimed and driven exclusively through this scan,
symmetrically, by whichever worker gets there first.

One exclusion DOES remain, for a different reason: a goal with an active
`Plan.goal_blocks` entry must never be offered as a candidate here. None of
`ExecutionHandler`'s five block-opening call sites transition the GOAL itself
to a terminal status when they open a block (only the plan-wide scalar used
to matter for that, by making the whole plan unclaimable) — under the new
per-goal design the plan often stays claimable while one goal is blocked, so
without this exclusion a blocked goal would be re-claimed and re-driven on
the very next scan, immediately colliding with its own still-active block
(`open_block`'s "already active" guard) in a hot poll-cadence-bounded loop.
`navigation.ready_goal_ids` has no concept of blocks (it is Plan-agnostic,
built only from `Goal.depends_on`), so this exclusion is applied here, using
the `Plan` this scan already has in hand — not pushed into the shared
navigation primitive.
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
        goals = plan.execution_goals
        ready_ids = ready_goal_ids(goals, now)
        blocked_goal_ids = {gid for gid, block in plan.goal_blocks.items() if block.active}
        enriched_ready_goal_ids = [
            goal.id
            for goal in goals
            if goal.id in ready_ids and goal.tasks and goal.id not in blocked_goal_ids
        ]
        for goal_id in enriched_ready_goal_ids:
            # goal_leases manages its own transaction per call (unlike
            # plans/executions/outbox, it is never bound to `with uow:`) --
            # calling it standalone here is correct, not an oversight.
            if uow.goal_leases.claim_one_ready_goal(plan_id, goal_id, worker_id, lease_seconds, now):
                return plan_id, goal_id
    return None
