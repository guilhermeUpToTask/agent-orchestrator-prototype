"""What one cycle acceptance run concluded.

Operational application state, alongside `promotion_records.py`: not a domain
aggregate and not telemetry. The verdict is **advisory** — nothing gates on it —
so it deliberately lives here rather than on the Plan, which is why the
acceptance run needed no domain un-freeze.

Kept out of `execution_records.py` for the reason `promotion_records.py` states:
that module's protocol already spans runs, attempts, planning operations and
runtime circuits, and another concern would make it harder to hold in context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from praxis_orchestrator.app.environment_port import AcceptanceOutcome, AcceptanceTrigger


@dataclass(frozen=True)
class AcceptanceRun:
    id: str
    plan_id: str
    cycle_id: str
    # None for a pre-publication run: it observes the whole cycle, not one goal.
    goal_id: str | None
    trigger: AcceptanceTrigger
    ref: str
    outcome: AcceptanceOutcome
    summary: str
    detail: str
    duration_seconds: float
    created_at: datetime


@runtime_checkable
class AcceptanceRunRepository(Protocol):
    """Transactional repository bound to the application UnitOfWork.

    Transactional rather than best-effort because an operator reads this at the
    publication gate to decide something: a verdict that silently failed to
    persist would be worse than one that was never run, since the absence of a
    row is how "not configured" is represented.
    """

    def add(self, run: AcceptanceRun) -> None: ...

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[AcceptanceRun]: ...

    def latest_for_cycle(self, plan_id: str, cycle_id: str) -> AcceptanceRun | None: ...
