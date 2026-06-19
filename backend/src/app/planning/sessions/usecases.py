from __future__ import annotations

from typing import Callable, Optional

import structlog

from src.app.planning.contracts.results import (
    ApprovalResult,
    ArchitectureResult,
    DiscoveryResult,
    GoalDispatchFailure,
    PhaseReviewResult,
)
from src.app.planning.sessions.support import PlanningSessionSupport
from src.app.services.decision_apply import apply_decision_to_spec
from src.app.usecases.goal_init import GoalInitUseCase
from src.domain.aggregates.planner_session import PlannerMode, PlannerSession, PlannerSessionStatus
from src.domain.aggregates.project_plan import ProjectPlan, ProjectPlanStatus
from src.domain.errors import InvalidPlanTransitionError
from src.domain.events.domain_event import DomainEvent
from src.domain.ports.messaging import EventPort
from src.domain.ports.planner import PlannerRuntimeError, PlannerRuntimePort
from src.domain.ports.project_state import ProjectStatePort
from src.domain.repositories.goal_repository import GoalRepositoryPort
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
        max_turns: int = 25,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support
        self._max_turns = max_turns

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

        # Replay a resumed session's saved transcript so the planner continues
        # in context instead of re-asking everything from scratch.
        prior_turns = [{"role": t.role, "content": t.content} for t in session.turns] or None

        try:
            output = self._runtime.run_session(
                prompt=self._support.build_discovery_prompt(),
                tools=tools,
                max_turns=self._max_turns,
                session_callback=self._support.make_session_callback(session),
                prior_turns=prior_turns,
            )
        except PlannerRuntimeError as exc:
            # A transient failure (provider timeout/blip, exhausted retries) keeps
            # the session RUNNING so it stays resumable; a permanent error (e.g.
            # tool-use unsupported) fails it terminally.
            if exc.transient:
                session.interrupt(reason=str(exc))
                self._session_repo.save(session)
                return DiscoveryResult(session.session_id, None, False, str(exc), resumable=True)
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
            raise InvalidPlanTransitionError(
                action="approve brief",
                current_status=plan.status.value,
                expected=[ProjectPlanStatus.DISCOVERY.value],
            )
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
        max_turns: int = 25,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support
        self._max_turns = max_turns

    def execute(
        self,
        io_handler: Optional[Callable[[str], str]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> ArchitectureResult:
        _ = io_handler
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.ARCHITECTURE:
            return ArchitectureResult(
                "", None, False, f"Plan must be in ARCHITECTURE state, got {plan.status.value}"
            )

        session = self._support.find_resumable_session(PlannerMode.ARCHITECTURE)
        if session is None:
            session = PlannerSession.create(self._support.build_architecture_prompt(), mode=PlannerMode.ARCHITECTURE)
            session.start()
            self._session_repo.save(session)

        # require_submit=False: the agent may finish by calling submit_architecture,
        # by exhausting the turn budget, or by user interrupt. In every case we keep
        # whatever was proposed and auto-finalize below if it is usable, instead of
        # discarding coherent work on a hard max-turns failure.
        try:
            output = self._runtime.run_session(
                prompt=self._support.build_architecture_prompt(),
                tools=self._support.build_architecture_tools(session),
                max_turns=self._max_turns,
                session_callback=self._support.make_session_callback(session),
                require_submit=False,
                cancel_check=cancel_check,
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            return ArchitectureResult(session.session_id, None, False, str(exc))

        assembly = self._support.assemble_roadmap(session)
        roadmap = assembly.roadmap

        if roadmap is None or not roadmap.decisions:
            detail = "; ".join(assembly.errors) if assembly.errors else (
                "need at least one decision and one phase"
            )
            reason = f"Architecture session ended without a usable roadmap ({detail})."
            session.fail(reason=reason)
            self._session_repo.save(session)
            return ArchitectureResult(session.session_id, None, False, reason)

        session.complete(reasoning=output.reasoning, raw_llm_output=output.raw_text, validation_errors=[], validation_warnings=[])
        self._session_repo.save(session)

        return ArchitectureResult(session.session_id, roadmap, True)


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
            raise InvalidPlanTransitionError(
                action="approve architecture",
                current_status=plan.status.value,
                expected=[ProjectPlanStatus.ARCHITECTURE.value],
            )

        arch_session = next((s for s in self._session_repo.list_all() if s.mode == PlannerMode.ARCHITECTURE and s.status == PlannerSessionStatus.COMPLETED), None)
        if arch_session is None:
            raise ValueError("No completed ARCHITECTURE session found")

        assembly = self._support.assemble_roadmap(arch_session)
        if assembly.roadmap is None:
            raise ValueError(
                "Completed ARCHITECTURE session has an invalid roadmap: "
                + "; ".join(assembly.errors)
            )
        roadmap = assembly.roadmap
        pending_decisions = roadmap.decisions
        pending_phases = roadmap.phases

        # An empty selection means "apply all proposed decisions" — that is how
        # the gate's default (every decision checked) and the rail's "Approve
        # architecture" send it. Treating [] as "apply none" silently dropped
        # the whole roadmap's decisions.
        apply_all = not decision_ids
        decisions_applied = 0
        spec_changes_applied = 0
        for entry in pending_decisions:
            if apply_all or entry.id in decision_ids:
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
        goals_failed: list[GoalDispatchFailure] = []
        if pending_phases:
            for goal_name in pending_phases[0].goal_names:
                goal_spec = roadmap.goal_specs.get(goal_name)
                if goal_spec:
                    try:
                        goal = self._goal_init.execute(goal_spec)
                        plan = plan.record_goal_registered(goal.name)
                        goals_dispatched.append(goal.goal_id)
                        if self._event_port is not None:
                            self._event_port.publish(DomainEvent(type="goal.unblocked", producer="planner-orchestrator", payload={"goal_id": goal.goal_id, "name": goal.name, "feature_tag": goal.feature_tag}))
                    except Exception as exc:
                        log.error("planner_orchestrator.goal_dispatch_failed", goal_name=goal_name, error=str(exc))
                        goals_failed.append(GoalDispatchFailure(goal_name=goal_name, error=str(exc)))

        self._plan_repo.save(plan)
        return ApprovalResult(decisions_applied, goals_dispatched, plan.status.value, spec_changes_applied, goals_failed)


class RunPhaseReviewUseCase:
    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        runtime: PlannerRuntimePort,
        support: PlanningSessionSupport,
        max_turns: int = 25,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._runtime = runtime
        self._support = support
        self._max_turns = max_turns

    def execute(
        self,
        io_handler: Optional[Callable[[str], str]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> PhaseReviewResult:
        _ = io_handler
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_REVIEW:
            return PhaseReviewResult("", "", None, [], False, f"Plan must be in PHASE_REVIEW state, got {plan.status.value}")

        session = self._support.find_resumable_session(PlannerMode.PHASE_REVIEW)
        if session is None:
            session = PlannerSession.create(self._support.build_phase_review_prompt(plan), mode=PlannerMode.PHASE_REVIEW)
            session.start()
            self._session_repo.save(session)

        # See RunArchitectureUseCase: finish via submit_review, budget, or interrupt,
        # and auto-finalize on usable output rather than hard-failing on max turns.
        try:
            output = self._runtime.run_session(
                prompt=self._support.build_phase_review_prompt(plan),
                tools=self._support.build_phase_review_tools(session, plan),
                max_turns=self._max_turns,
                session_callback=self._support.make_session_callback(session),
                require_submit=False,
                cancel_check=cancel_check,
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            return PhaseReviewResult(session.session_id, "", None, [], False, str(exc))

        lessons = self._support.extract_review_lessons(session)
        next_phase = self._support.extract_next_phase(session)
        pending_decisions = self._support.extract_pending_decisions(session)

        if not lessons:
            reason = "Phase review session ended without recording lessons."
            session.fail(reason=reason)
            self._session_repo.save(session)
            return PhaseReviewResult(session.session_id, "", None, [], False, reason)

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
            raise InvalidPlanTransitionError(
                action="approve phase review",
                current_status=plan.status.value,
                expected=[ProjectPlanStatus.PHASE_REVIEW.value],
            )

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
        goals_failed: list[GoalDispatchFailure] = []
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
                        goals_failed.append(GoalDispatchFailure(goal_name=goal_name, error=str(exc)))
        else:
            plan = plan.mark_done()
            self._plan_repo.save(plan)

        self._plan_repo.save(plan)
        return ApprovalResult(decisions_applied, goals_dispatched, plan.status.value, goals_failed=goals_failed)


class ResumePhaseDispatchUseCase:
    """Re-dispatch active-phase goals that never got created.

    Recovery path for a *partial* goal-dispatch failure. When approve-architecture
    (or approve-phase) dispatched some of a phase's goals but one or more failed
    — e.g. a transient git error during ``create_goal_branch`` — those goal names
    end up with no GoalAggregate. Nothing retries them automatically: the
    Reconciler only watches in-flight *tasks* and PR-phase goals, so a missing
    (or never-tasked PENDING) goal is invisible to it. The goal spec it needs to
    rebuild the branch + tasks lives in the completed planner session, not in the
    goal repo — which is why the retry belongs here in the planner layer.

    Idempotent: goal names that already have an aggregate are skipped, so this is
    safe to call repeatedly. Any goal stuck as a branch-less PENDING zombie must
    first be removed (quarantined) so its name reads as missing again.
    """

    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        goal_repo: GoalRepositoryPort,
        goal_init: GoalInitUseCase,
        support: PlanningSessionSupport,
        event_port: Optional[EventPort] = None,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._goal_repo = goal_repo
        self._goal_init = goal_init
        self._support = support
        self._event_port = event_port

    def execute(self) -> ApprovalResult:
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_ACTIVE:
            raise InvalidPlanTransitionError(
                action="resume phase dispatch",
                current_status=plan.status.value,
                expected=[ProjectPlanStatus.PHASE_ACTIVE.value],
            )

        phase = plan.current_phase()
        if phase is None:
            raise ValueError("No active phase to resume dispatch for")

        existing = {g.name for g in self._goal_repo.list_all()}
        missing = [name for name in phase.goal_names if name not in existing]

        # Specs for the active phase live in whichever completed planner session
        # proposed it (architecture for phase 0, phase-review for later phases).
        completed_sessions = [
            s
            for s in self._session_repo.list_all()
            if s.status == PlannerSessionStatus.COMPLETED
            and s.mode in (PlannerMode.ARCHITECTURE, PlannerMode.PHASE_REVIEW)
        ]

        goals_dispatched: list[str] = []
        goals_failed: list[GoalDispatchFailure] = []
        for goal_name in missing:
            goal_spec = self._lookup_spec(completed_sessions, goal_name)
            if goal_spec is None:
                goals_failed.append(GoalDispatchFailure(
                    goal_name=goal_name,
                    error="No goal spec found in completed planner sessions.",
                ))
                continue
            try:
                goal = self._goal_init.execute(goal_spec)
                plan = plan.record_goal_registered(goal.name)
                goals_dispatched.append(goal.goal_id)
                if self._event_port is not None:
                    self._event_port.publish(DomainEvent(
                        type="goal.unblocked",
                        producer="planner-orchestrator",
                        payload={"goal_id": goal.goal_id, "name": goal.name, "feature_tag": goal.feature_tag},
                    ))
            except Exception as exc:
                log.error("planner_orchestrator.goal_dispatch_failed", goal_name=goal_name, error=str(exc))
                goals_failed.append(GoalDispatchFailure(goal_name=goal_name, error=str(exc)))

        self._plan_repo.save(plan)
        return ApprovalResult(0, goals_dispatched, plan.status.value, goals_failed=goals_failed)

    def _lookup_spec(self, sessions: list[PlannerSession], goal_name: str):  # type: ignore[no-untyped-def]
        """Return the first GoalSpec for *goal_name* across completed sessions."""
        for session in sessions:
            spec = self._support.find_goal_spec(session, goal_name)
            if spec is not None:
                return spec
        return None
