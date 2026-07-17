"""Lease-safe startup reconciliation for operational execution records."""

from __future__ import annotations

from src.app.execution_records import ExecutionAttemptStatus, ExecutionRunStatus
from src.app.ports import Clock, UnitOfWork


def reconcile_stale_attempts(uow: UnitOfWork, clock: Clock) -> list[str]:
    """Abandon RUNNING attempts whose owning plan has no live worker lease.

    Task state intentionally remains RUNNING. The next lease holder re-enters
    the existing reclaim choreography, starts a fresh run/attempt identity, and
    never mistakes a dead process for work still in flight.
    """
    reconciled: list[str] = []
    with uow:
        for attempt in uow.executions.list_open_attempts():
            if uow.plans.is_claim_live(attempt.plan_id):
                continue
            uow.executions.finalize_attempt(
                attempt.id,
                attempt_status=ExecutionAttemptStatus.ABANDONED,
                run_status=ExecutionRunStatus.ABANDONED,
                completed_at=clock.now(),
            )
            reconciled.append(attempt.id)
    return reconciled
