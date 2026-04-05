"""
src/app/usecases/planner_orchestrator.py — Thin facade for planning operations.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.app.planning.contracts.results import (
    ApprovalResult,
    ArchitectureResult,
    DiscoveryResult,
    PhaseReviewResult,
)
from src.app.planning.sessions.support import PlanningSessionSupport
from src.app.planning.sessions.usecases import (
    ApproveArchitectureUseCase,
    ApproveBriefUseCase,
    ApprovePhaseReviewUseCase,
    RunArchitectureUseCase,
    RunPhaseReviewUseCase,
    StartDiscoveryUseCase,
)
from src.app.services.planner_context import PlannerContextAssembler
from src.app.usecases.goal_init import GoalInitUseCase
from src.app.usecases.validate_against_spec import ValidateAgainstSpec
from src.domain.aggregates.project_plan import ProjectPlan
from src.domain.ports.messaging import EventPort
from src.domain.ports.planner import PlannerRuntimePort
from src.domain.ports.project_state import ProjectStatePort
from src.domain.repositories.agent_registry import AgentRegistryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.planner_session_repository import PlannerSessionRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort
from src.domain.project_spec import ProjectSpecRepository


class PlannerOrchestrator:
    """Facade that delegates planning actions to focused use-cases."""

    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        context_assembler: PlannerContextAssembler,
        autonomous_runtime: PlannerRuntimePort,
        interactive_runtime: PlannerRuntimePort,
        goal_init: GoalInitUseCase,
        validator: ValidateAgainstSpec,
        project_state: ProjectStatePort,
        agent_registry: AgentRegistryPort,
        goal_repo: GoalRepositoryPort,
        spec_repo: ProjectSpecRepository,
        project_name: str,
        event_port: Optional[EventPort] = None,
    ) -> None:
        _ = validator
        _ = agent_registry

        self._plan_repo = plan_repo
        self._turn_callback: Optional[Callable[[str, list], None]] = None
        self._planner_event_hook: Optional[Callable[[str, dict], None]] = None
        self._support = PlanningSessionSupport(
            context_assembler=context_assembler,
            session_repo=session_repo,
            plan_repo=plan_repo,
            goal_repo=goal_repo,
        )

        self._start_discovery = StartDiscoveryUseCase(
            plan_repo=plan_repo,
            session_repo=session_repo,
            runtime=interactive_runtime,
            support=self._support,
        )
        self._approve_brief = ApproveBriefUseCase(plan_repo=plan_repo)
        self._run_architecture = RunArchitectureUseCase(
            plan_repo=plan_repo,
            session_repo=session_repo,
            runtime=autonomous_runtime,
            support=self._support,
        )
        self._approve_architecture = ApproveArchitectureUseCase(
            plan_repo=plan_repo,
            session_repo=session_repo,
            project_state=project_state,
            goal_init=goal_init,
            spec_repo=spec_repo,
            project_name=project_name,
            support=self._support,
            event_port=event_port,
        )
        self._run_phase_review = RunPhaseReviewUseCase(
            plan_repo=plan_repo,
            session_repo=session_repo,
            runtime=autonomous_runtime,
            support=self._support,
        )
        self._approve_phase_review = ApprovePhaseReviewUseCase(
            plan_repo=plan_repo,
            session_repo=session_repo,
            project_state=project_state,
            goal_init=goal_init,
            spec_repo=spec_repo,
            project_name=project_name,
            support=self._support,
        )

    def start_discovery(self, io_handler: Optional[Callable[[str], str]] = None) -> DiscoveryResult:
        return self._start_discovery.execute(io_handler=io_handler)

    def approve_brief(self) -> ProjectPlan:
        return self._approve_brief.execute()

    def run_architecture(self, io_handler: Optional[Callable[[str], str]] = None) -> ArchitectureResult:
        return self._run_architecture.execute(io_handler=io_handler)

    def approve_architecture(self, decision_ids: list[str]) -> ApprovalResult:
        return self._approve_architecture.execute(decision_ids=decision_ids)

    def run_phase_review(self, io_handler: Optional[Callable[[str], str]] = None) -> PhaseReviewResult:
        return self._run_phase_review.execute(io_handler=io_handler)

    def approve_phase_review(self, approve_next: bool = True) -> ApprovalResult:
        return self._approve_phase_review.execute(approve_next=approve_next)

    def get_status(self) -> ProjectPlan:
        return self._plan_repo.load()

    def set_turn_callback(self, callback: Optional[Callable[[str, list], None]]) -> None:
        self._turn_callback = callback
        self._support.set_turn_callback(callback)

    def set_planner_event_hook(self, hook: Optional[Callable[[str, dict], None]]) -> None:
        self._planner_event_hook = hook
        self._support.set_planner_event_hook(hook)
