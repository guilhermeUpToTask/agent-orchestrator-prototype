"""In-memory acceptance-run ledger, mirroring the SQLite adapter's semantics.

Fake/real parity is an invariant here, not a convenience: the orchestration
truth test runs the same suite against both, and a fake with looser transaction
semantics would let a rollback bug pass on the fake and fail on SQLite.
"""

from __future__ import annotations

from agent_orchestrator.app.acceptance_records import AcceptanceRun


class InMemoryAcceptanceRunRepository:
    def __init__(self) -> None:
        self._committed: list[AcceptanceRun] = []
        self._staged: list[AcceptanceRun] | None = None

    def _begin(self) -> None:
        self._staged = []

    def _commit(self) -> None:
        assert self._staged is not None
        self._committed.extend(self._staged)
        self._staged = None

    def _rollback(self) -> None:
        self._staged = None

    def _bound(self) -> list[AcceptanceRun]:
        if self._staged is None:
            raise RuntimeError(
                "InMemoryAcceptanceRunRepository used outside a UnitOfWork transaction"
            )
        return self._staged

    def add(self, run: AcceptanceRun) -> None:
        staged = self._bound()
        if any(item.id == run.id for item in (*self._committed, *staged)):
            raise RuntimeError(f"duplicate acceptance run {run.id!r}")
        staged.append(run)

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[AcceptanceRun]:
        staged = self._staged or []
        return sorted(
            (
                item
                for item in (*self._committed, *staged)
                if item.plan_id == plan_id and item.cycle_id == cycle_id
            ),
            key=lambda item: (item.created_at, item.id),
        )

    def latest_for_cycle(self, plan_id: str, cycle_id: str) -> AcceptanceRun | None:
        runs = self.list_for_cycle(plan_id, cycle_id)
        return runs[-1] if runs else None
