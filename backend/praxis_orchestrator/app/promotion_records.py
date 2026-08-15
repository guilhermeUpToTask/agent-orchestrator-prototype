"""Where a promoted goal's work landed.

Operational application state, like the run/attempt ledger in
`execution_records.py`: not a domain aggregate and not telemetry. It records the
branches the workspace adapter ACTUALLY merged and the SHA the merge produced,
so "where did this goal's code go" is answerable without reconstructing a name
from a convention.

Kept out of `execution_records.py` deliberately: that module's repository
protocol already spans runs, attempts, planning operations and runtime circuits,
and a fifth concern would make it harder to hold in context, not easier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GoalPromotion:
    id: str
    plan_id: str
    cycle_id: str
    goal_id: str
    # Stored as the adapter built them, never re-derived at read time.
    from_ref: str
    into_ref: str
    merge_sha: str
    promoted_at: datetime


@runtime_checkable
class GoalPromotionRepository(Protocol):
    """Transactional repository bound to the application UnitOfWork.

    Deliberately NOT the best-effort pattern used by `workers`/`agent_events`:
    those are telemetry, this is evidence.
    """

    def add(self, promotion: GoalPromotion) -> None: ...

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]: ...
