from __future__ import annotations

from typing import Callable, Optional

import structlog

from src.app.planning.contracts.results import (
    ApprovalResult,
    ArchitectureResult,
    DiscoveryResult,
    PhaseReviewResult,
)
from src.app.planning.sessions.support import PlanningSessionSupport
from src.app.services.decision_apply import apply_decision_to_spec
from src.app.usecases.goal_init import GoalInitUseCase
from src.domain.aggregates.planner_session import PlannerMode, PlannerSession, PlannerSessionStatus
from src.domain.aggregates.project_plan import ProjectPlan, ProjectPlanStatus
from src.domain.events.domain_event import DomainEvent
from src.domain.ports.messaging import EventPort
from src.domain.ports.planner import PlannerRuntimeError, PlannerRuntimePort
from src.domain.ports.project_state import ProjectStatePort
from src.domain.repositories.planner_session_repository import PlannerSessionRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort
from src.domain.project_spec import ProjectSpecRepository

log = structlog.get_logger(__name__)


class StartDiscoveryUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        runtime: PlannerRuntimePort,
        support: PlanningSessionSupport,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support

    def execute(self, io_handler: Optional[Callable[[str], str]] = None) -> DiscoveryResult:
        plan = self._plan_repo.get()
        if plan is not None and plan.status != ProjectPlanStatus.DISCOVERY:
            return DiscoveryResult("", None, False, f"Cannot start discovery: plan is in {plan.status.value} state")

        session = self._support.find_resumable_session(PlannerMode.DISCOVERY)
        if session is None:
            session = PlannerSession.create(
                "Describe the project vision and gather requirements through questions.",
                mode=PlannerMode.DISCOVERY,
            )
            session.start()
            self._session_repo.save(session)

        tools = self._support.build_discovery_tools(session, io_handler)

        try:
            output = self._runtime.run_session(
                prompt=self._support.build_discovery_prompt(),
                tools=tools,
                max_turns=20,
                session_callback=self._support.make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            return DiscoveryResult(session.session_id, None, False, str(exc))

        try:
            brief = self._support.parse_brief(output.roadmap_raw)
        except ValueError as exc:
            session.fail(reason=f"Failed to parse brief: {exc}")
            self._session_repo.save(session)
            return DiscoveryResult(session.session_id, None, False, f"LLM returned invalid schema: {exc}")

        session.record_roadmap_candidate({"brief": output.roadmap_raw})
        self._session_repo.save(session)
        session.complete(reasoning=output.reasoning, raw_llm_output=output.raw_text, validation_errors=[], validation_warnings=[])
        self._session_repo.save(session)

        if plan is None:
            plan = ProjectPlan.create(brief.vision)
        plan = plan.model_copy(update={"brief": brief})
        self._plan_repo.save(plan)

        return DiscoveryResult(session.session_id, brief, True)


class ApproveBriefUseCase:
    def __init__(self, plan_repo: ProjectPlanRepositoryPort) -> None:
        self._plan_repo = plan_repo

    def execute(self) -> ProjectPlan:
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.DISCOVERY:
            raise ValueError(f"Plan must be in DISCOVERY state, got {plan.status.value}")
        if plan.brief is None:
            raise ValueError("No brief to approve on the plan")
        plan = plan.approve_brief(plan.brief)
        self._plan_repo.save(plan)
        return plan


class RunArchitectureUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        runtime: PlannerRuntimePort,
        support: PlanningSessionSupport,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support

    def execute(self, io_handler: Optional[Callable[[str], str]] = None) -> ArchitectureResult:
        _ = io_handler
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.ARCHITECTURE:
            return ArchitectureResult("", [], [], False, f"Plan must be in ARCHITECTURE state, got {plan.status.value}")

        session = self._support.find_resumable_session(PlannerMode.ARCHITECTURE)
        if session is None:
            session = PlannerSession.create(self._support.build_architecture_prompt(), mode=PlannerMode.ARCHITECTURE)
            session.start()
            self._session_repo.save(session)

        try:
            output = self._runtime.run_session(
                prompt=self._support.build_architecture_prompt(),
                tools=self._support.build_architecture_tools(session),
                max_turns=15,
                session_callback=self._support.make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            return ArchitectureResult(session.session_id, [], [], False, str(exc))

        pending_decisions = self._support.extract_pending_decisions(session)
        pending_phases = self._support.extract_pending_phases(session)
        session.complete(reasoning=output.reasoning, raw_llm_output=output.raw_text, validation_errors=[], validation_warnings=[])
        self._session_repo.save(session)

        return ArchitectureResult(session.session_id, pending_decisions, pending_phases, True)


class ApproveArchitectureUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        project_state: ProjectStatePort,
        goal_init: GoalInitUseCase,
        spec_repo: ProjectSpecRepository,
        project_name: str,
        support: PlanningSessionSupport,
        event_port: Optional[EventPort],
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._project_state = project_state
        self._goal_init = goal_init
        self._spec_repo = spec_repo
        self._project_name = project_name
        self._support = support
        self._event_port = event_port

    def execute(self, decision_ids: list[str]) -> ApprovalResult:
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.ARCHITECTURE:
            raise ValueError(f"Plan must be in ARCHITECTURE state, got {plan.status.value}")

        arch_session = next((s for s in self._session_repo.list_all() if s.mode == PlannerMode.ARCHITECTURE and s.status == PlannerSessionStatus.COMPLETED), None)
        if arch_session is None:
            raise ValueError("No completed ARCHITECTURE session found")

        pending_decisions = self._support.extract_pending_decisions(arch_session)
        pending_phases = self._support.extract_pending_phases(arch_session)

        decisions_applied = 0
        spec_changes_applied = 0
        for entry in pending_decisions:
            if entry.id in decision_ids:
                self._project_state.write_decision(entry)
                decisions_applied += 1
                if entry.spec_changes and not entry.spec_changes.is_empty:
                    try:
                        if apply_decision_to_spec(entry, self._spec_repo, self._project_name):
                            spec_changes_applied += 1
                    except Exception as exc:
                        log.error("planner_orchestrator.spec_apply_failed", decision_id=entry.id, error=str(exc))

        plan = plan.approve_phase(pending_phases)
        self._plan_repo.save(plan)

        goals_dispatched: list[str] = []
        if pending_phases:
            for goal_name in pending_phases[0].goal_names:
                goal_spec = self._support.find_goal_spec(arch_session, goal_name)
                if goal_spec:
                    try:
                        goal = self._goal_init.execute(goal_spec)
                        plan = plan.record_goal_registered(goal.name)
                        goals_dispatched.append(goal.goal_id)
                        if self._event_port is not None:
                            self._event_port.publish(DomainEvent(type="goal.unblocked", producer="planner-orchestrator", payload={"goal_id": goal.goal_id, "name": goal.name, "feature_tag": goal.feature_tag}))
                    except Exception as exc:
                        log.error("planner_orchestrator.goal_dispatch_failed", goal_name=goal_name, error=str(exc))

        self._plan_repo.save(plan)
        return ApprovalResult(decisions_applied, goals_dispatched, plan.status.value, spec_changes_applied)


class RunPhaseReviewUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        runtime: PlannerRuntimePort,
        support: PlanningSessionSupport,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support

    def execute(self, io_handler: Optional[Callable[[str], str]] = None) -> PhaseReviewResult:
        _ = io_handler
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_REVIEW:
            return PhaseReviewResult("", "", None, [], False, f"Plan must be in PHASE_REVIEW state, got {plan.status.value}")

        session = self._support.find_resumable_session(PlannerMode.PHASE_REVIEW)
        if session is None:
            session = PlannerSession.create(self._support.build_phase_review_prompt(plan), mode=PlannerMode.PHASE_REVIEW)
            session.start()
            self._session_repo.save(session)

        try:
            output = self._runtime.run_session(
                prompt=self._support.build_phase_review_prompt(plan),
                tools=self._support.build_phase_review_tools(session, plan),
                max_turns=15,
                session_callback=self._support.make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            return PhaseReviewResult(session.session_id, "", None, [], False, str(exc))

        lessons = self._support.extract_review_lessons(session)
        next_phase = self._support.extract_next_phase(session)
        pending_decisions = self._support.extract_pending_decisions(session)
        session.complete(reasoning=output.reasoning, raw_llm_output=output.raw_text, validation_errors=[], validation_warnings=[])
        self._session_repo.save(session)

        return PhaseReviewResult(session.session_id, lessons, next_phase, pending_decisions, True)


class ApprovePhaseReviewUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        project_state: ProjectStatePort,
        goal_init: GoalInitUseCase,
        spec_repo: ProjectSpecRepository,
        project_name: str,
        support: PlanningSessionSupport,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._project_state = project_state
        self._goal_init = goal_init
        self._spec_repo = spec_repo
        self._project_name = project_name
        self._support = support

    def execute(self, approve_next: bool = True) -> ApprovalResult:
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_REVIEW:
            raise ValueError(f"Plan must be in PHASE_REVIEW state, got {plan.status.value}")

        review_session = next((s for s in self._session_repo.list_all() if s.mode == PlannerMode.PHASE_REVIEW and s.status == PlannerSessionStatus.COMPLETED), None)
        if review_session is None:
            raise ValueError("No completed PHASE_REVIEW session found")

        lessons = self._support.extract_review_lessons(review_session)
        next_phase = self._support.extract_next_phase(review_session)
        pending_decisions = self._support.extract_pending_decisions(review_session)
        architecture_summary = self._support.extract_architecture_summary(review_session)

        plan = plan.complete_review(lessons, architecture_summary)

        decisions_applied = 0
        for entry in pending_decisions:
            self._project_state.write_decision(entry)
            decisions_applied += 1
            if entry.spec_changes and not entry.spec_changes.is_empty:
                try:
                    apply_decision_to_spec(entry, self._spec_repo, self._project_name)
                except Exception as exc:
                    log.error("planner_orchestrator.spec_apply_failed", decision_id=entry.id, error=str(exc))

        goals_dispatched: list[str] = []
        if approve_next and next_phase:
            plan = plan.approve_phase([next_phase])
            self._plan_repo.save(plan)

            for goal_name in next_phase.goal_names:
                goal_spec = self._support.find_goal_spec(review_session, goal_name)
                if goal_spec:
                    try:
                        goal = self._goal_init.execute(goal_spec)
                        plan = plan.record_goal_registered(goal.name)
                        goals_dispatched.append(goal.goal_id)
                    except Exception as exc:
                        log.error("planner_orchestrator.goal_dispatch_failed", goal_name=goal_name, error=str(exc))
        else:
            plan = plan.mark_done()
            self._plan_repo.save(plan)

        self._plan_repo.save(plan)
        return ApprovalResult(decisions_applied, goals_dispatched, plan.status.value)
