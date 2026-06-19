"""
src/app/reconciliation/phase_dispatch_reconciler.py — Phase-dispatch control loop.

Promotes ``ResumePhaseDispatchUseCase`` from an operator-triggered recovery to a
level-triggered backstop. On each pass it compares the *desired* state (the
active phase's ``goal_names``, declared by the ProjectPlan) against the
*observed* state (goals present in the GoalRepo). Any goal name with no
aggregate is a divergence — typically a dispatch whose ``goal.unblocked`` event
never fired because branch creation failed. Nothing else heals this: the task
watchdog only sees tasks, the PR loop only sees PR-phase goals.

Crucially, this loop only *detects*; the corrective action is delegated to the
planner-layer use case (passed in as ``resume_dispatch``), because the
``GoalSpec`` needed to rebuild a goal lives in the planner session, not here.
The loop stays thin and the layering holds.
"""
from __future__ import annotations

from typing import Any, Callable

import structlog

from src.app.reconciliation.control_loop import ControlLoop
from src.domain.aggregates.project_plan import ProjectPlanStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort

log = structlog.get_logger(__name__)


class PhaseDispatchReconciler(ControlLoop):
    """Re-dispatches active-phase goals that never got created."""

    name = "phase_dispatch"

    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        goal_repo: GoalRepositoryPort,
        resume_dispatch: Callable[[], Any],
        interval_seconds: int = 120,
    ) -> None:
        self._plan_repo = plan_repo
        self._goal_repo = goal_repo
        self._resume = resume_dispatch
        self.interval_seconds = interval_seconds

    def reconcile_once(self) -> None:
        try:
            plan = self._plan_repo.load()
        except KeyError:
            return  # no plan yet — nothing to reconcile

        if plan.status != ProjectPlanStatus.PHASE_ACTIVE:
            return

        phase = plan.current_phase()
        if phase is None:
            return

        existing = {g.name for g in self._goal_repo.list_all()}
        missing = [name for name in phase.goal_names if name not in existing]
        if not missing:
            return  # converged — stay quiet

        log.warning(
            "reconciler.phase_dispatch.divergence_detected",
            phase=phase.name,
            missing=missing,
        )
        result = self._resume()  # delegate to the planner-layer use case
        log.info(
            "reconciler.phase_dispatch.redispatched",
            dispatched=list(result.goals_dispatched),
            failed=[f.goal_name for f in result.goals_failed],
        )
