from __future__ import annotations

from typing import Protocol

from src.domain.aggregates.planner_orchestrator import Plan


class PlanRepository(Protocol):
    """Single source of truth for plan persistence + the concurrency primitives.

    This is the ONE contract the application use cases and the infra adapter share
    (no parallel "...Like" duplicate). An infra adapter implements it against
    SQLite.

    Persistence:
      get  / save  — save() does an optimistic-lock compare-and-swap on `version`
                     and raises StaleVersionError on conflict (worker-vs-edit race).

    Idempotency:
      find_by_request_id / bind_request_id — API-layer create idempotency: a
      retried create returns the same plan id instead of duplicating.

    Liveness / crash recovery (the lease — replaces the old reconciler):
      claim_one_unit — claim a plan needing work; only an unclaimed or
                       lease-expired plan is claimable, so a dead worker's plan is
                       reclaimable by another.
      heartbeat      — renew the lease while a worker is actively advancing a plan.
      release        — free the claim on pause/done/fail/crash.
    """

    # --- persistence ---
    def get(self, plan_id: str) -> Plan: ...
    def save(self, plan: Plan) -> None: ...  # version CAS -> StaleVersionError

    def delete(self, plan_id: str) -> None:
        """Remove a plan and everything produced under it.

        Not a lifecycle transition — the cyclic root is never terminal, so
        "finished with this plan" has no in-domain representation. This is the
        operator disposing of a plan entirely, which is why it lives on the
        repository rather than on the aggregate.

        Implementations must leave no orphan behind: several plan-scoped tables
        carry no `ON DELETE CASCADE`, and two more reference a plan without a
        foreign key at all, so a bare `DELETE FROM plans` silently leaks rows.
        Raises `PlanNotFoundError` when the plan does not exist.
        """
        ...

    # --- create idempotency ---
    def find_by_project_id(self, project_id: str) -> str | None: ...
    def find_by_request_id(self, request_id: str) -> str | None: ...
    def bind_request_id(self, request_id: str, plan_id: str) -> None: ...

    # --- lease (liveness / crash recovery) ---
    def claim_one_unit(self, worker_id: str, lease_seconds: int) -> Plan | None: ...
    def is_claim_live(self, plan_id: str) -> bool: ...
    def heartbeat(self, plan_id: str, worker_id: str) -> None: ...
    def release(self, plan_id: str, worker_id: str) -> None: ...

    # --- goal-level parallelism scan (ADR-001, domain unfreeze #13) ---
    def list_running_ids(self, limit: int) -> list[str]:
        """Cheap, indexed candidate scan (oldest-updated first, so one busy
        plan can't starve the rest) for the goal-lease claim use case to
        reconstruct and check for a ready-and-unenriched goal. Deliberately
        NOT a full Plan reconstruction — that happens one candidate at a time
        in the use-case layer, bounded by `limit`."""
        ...
