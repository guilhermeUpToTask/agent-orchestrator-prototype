"""delete_plan — operator disposal of a plan and everything under it.

The cyclic root is never terminal: a `ProjectDefinition` owns exactly one
long-lived `Plan` (ADR-003), so "I am finished with this plan" has no in-domain
representation and no lifecycle transition can express it. Disposal is therefore
an operator command against the repository, not a state change.

It exists because without it there is no supported way to start a genuinely
fresh run: re-posting a brief to the same project reopens the SAME plan and adds
another cycle. The happy-path fixture needs each measured run to begin from
nothing, and the alternative — deleting rows out of SQLite from a shell script —
is exactly the database surgery the control plane is supposed to remove.

Deliberately NOT emitting a domain event: the outbox rows for this plan are
deleted in the same transaction, so an event about the deletion would either be
destroyed with them or describe an aggregate no consumer can resolve.
"""

from __future__ import annotations

from praxis_orchestrator.app.ports import UnitOfWork
from praxis_orchestrator.domain.errors.planning_errors import PlanBusyError


def delete_plan(plan_id: str, uow: UnitOfWork) -> None:
    """Delete `plan_id`, refusing while a worker holds a live lease.

    Raises `PlanNotFoundError` if it does not exist, `PlanBusyError` if claimed.
    """
    with uow:
        uow.plans.get(plan_id)  # PlanNotFoundError — 404 rather than silent success
        if uow.plans.is_claim_live(plan_id):
            # A live lease means an agent or reasoner call is in flight. Its
            # finalize transaction re-reads this plan and re-guards on version;
            # deleting now turns that into a crash after the side effect landed.
            raise PlanBusyError(plan_id)
        uow.plans.delete(plan_id)
