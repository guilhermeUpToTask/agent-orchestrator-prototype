"""Lease-safe startup reconciliation for operational execution records."""

from __future__ import annotations

from src.app.execution_records import ExecutionAttemptStatus, ExecutionRunStatus
from src.app.ports import Clock, UnitOfWork


def reconcile_stale_attempts(uow: UnitOfWork, clock: Clock) -> list[str]:
    """Abandon RUNNING attempts that NO live lease owns — plan or goal.

    Task state intentionally remains RUNNING. The next lease holder re-enters
    the existing reclaim choreography, starts a fresh run/attempt identity, and
    never mistakes a dead process for work still in flight.

    BOTH leases have to be checked. The plan claim alone was correct only before
    goal leases (ADR-001 / un-freeze #13): attempts are now created by goal
    workers, and a goal worker does not hold the plan claim while it runs. A
    second worker's STARTUP reconciliation therefore saw a RUNNING attempt with
    no live plan claim and abandoned a ledger row whose process was alive and
    about to finalize it. Single-worker restart never exposed it — there the old
    process really is dead — so it needs two workers to appear at all.
    """
    reconciled: list[str] = []
    with uow:
        for attempt in uow.executions.list_open_attempts():
            if uow.plans.is_claim_live(attempt.plan_id):
                continue
            if uow.goal_leases.is_claim_live(attempt.plan_id, attempt.goal_id, clock.now()):
                continue
            uow.executions.finalize_attempt(
                attempt.id,
                attempt_status=ExecutionAttemptStatus.ABANDONED,
                run_status=ExecutionRunStatus.ABANDONED,
                completed_at=clock.now(),
            )
            reconciled.append(attempt.id)
    return reconciled
