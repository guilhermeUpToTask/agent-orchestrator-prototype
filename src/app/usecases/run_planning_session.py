"""
Deprecated compatibility wrapper for the old planning pipeline.

The full planning pipeline that used to live here has been intentionally
removed as part of the phase-5 refactor. Use PlannerOrchestrator instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlanningResult:
    session_id: str
    roadmap: Optional[object] = None
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    goals_dispatched: list[str] = field(default_factory=list)
    failure_reason: Optional[str] = None

    @property
    def has_errors(self) -> bool:
        return bool(self.validation_errors)

    @property
    def dispatched_count(self) -> int:
        return len(self.goals_dispatched)


class RunPlanningSessionUseCase:
    """Deprecated shim. All behavior moved to PlannerOrchestrator."""

    def __init__(self, *args, **kwargs) -> None:
        import warnings

        warnings.warn(
            "RunPlanningSessionUseCase is deprecated; use PlannerOrchestrator instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def execute(self, user_input: str, dispatch: bool = False) -> PlanningResult:
        _ = user_input
        _ = dispatch
        return PlanningResult(
            session_id="",
            failure_reason=(
                "RunPlanningSessionUseCase has been removed. "
                "Use PlannerOrchestrator.start_discovery/run_architecture/run_phase_review."
            ),
        )

    def dispatch_roadmap(self, session_id: str) -> PlanningResult:
        _ = session_id
        return PlanningResult(
            session_id="",
            failure_reason=(
                "dispatch_roadmap is no longer supported in RunPlanningSessionUseCase. "
                "Use PlannerOrchestrator approval flows instead."
            ),
        )
