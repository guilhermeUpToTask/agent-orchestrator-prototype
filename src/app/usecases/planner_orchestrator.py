"""
src/app/usecases/planner_orchestrator.py — Primary entry point for all planning operations.

This orchestrator reads ProjectPlan.status and routes to the correct mode.
It replaces RunPlanningSessionUseCase as the user-facing planning interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

import structlog

from src.app.services.decision_apply import apply_decision_to_spec
from src.app.services.planner_context import PlannerContextAssembler
from src.app.usecases.goal_init import GoalInitUseCase
from src.app.usecases.validate_against_spec import ValidateAgainstSpec
from src.domain.events.domain_event import DomainEvent
from src.domain.ports.messaging import EventPort
from src.domain.aggregates.planner_session import (    PlannerMode,
    PlannerSession,
    PlannerSessionStatus,
)
from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectBrief,
    ProjectPlan,
    ProjectPlanStatus,
)
from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)
from src.domain.ports.project_state import DecisionEntry, ProjectStatePort, SpecChanges
from src.domain.repositories.agent_registry import AgentRegistryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.planner_session_repository import (
    PlannerSessionRepositoryPort,
)
from src.domain.repositories.project_plan_repository import (
    ProjectPlanRepositoryPort,
)
from src.domain.project_spec import ProjectSpecRepository

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

# TODO: refactor and break this orchestrator in uses cases
# TODO: enrich the error handleling to differenciate wrong json schemas, llm calling timout, token limit exceed, etc...
# TODO: wrap all error in telemetry aswell
# TODO: Should inject project spec aswell to enrich the planing mode, because its making the same questions we alredy answered in the project spec.
# TODO: show more logs of lllm clas in the architect mode


@dataclass
class DiscoveryResult:
    session_id: str
    brief: Optional[ProjectBrief]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class ArchitectureResult:
    session_id: str
    pending_decisions: list[DecisionEntry]
    pending_phases: list[Phase]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class PhaseReviewResult:
    session_id: str
    lessons: str
    next_phase_proposal: Optional[Phase]
    pending_decisions: list[DecisionEntry]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class ApprovalResult:
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str
    spec_changes_applied: int = 0


# ---------------------------------------------------------------------------
# PlannerOrchestrator
# ---------------------------------------------------------------------------


class PlannerOrchestrator:
    """
    Primary entry point for all planning operations.

    Routes to the correct mode based on ProjectPlan.status and handles
    resumable sessions. Replaces RunPlanningSessionUseCase as the primary
    user-facing planning interface.
    """

    def __init__(
        self,
        plan_repo: ProjectPlanRepositoryPort,
        session_repo: PlannerSessionRepositoryPort,
        context_assembler: PlannerContextAssembler,
        autonomous_runtime: PlannerRuntimePort,  # AnthropicPlannerRuntime
        interactive_runtime: PlannerRuntimePort,  # InteractivePlannerRuntime
        goal_init: GoalInitUseCase,
        validator: ValidateAgainstSpec,
        project_state: ProjectStatePort,
        agent_registry: AgentRegistryPort,
        goal_repo: GoalRepositoryPort,
        spec_repo: ProjectSpecRepository,
        project_name: str,
        event_port: Optional[EventPort] = None,
    ) -> None:
        self._plan_repo = plan_repo
        self._session_repo = session_repo
        self._context_assembler = context_assembler
        self._autonomous_runtime = autonomous_runtime
        self._interactive_runtime = interactive_runtime
        self._goal_init = goal_init
        self._validator = validator
        self._project_state = project_state
        self._agent_registry = agent_registry
        self._goal_repo = goal_repo
        self._spec_repo = spec_repo
        self._project_name = project_name
        self._event_port = event_port

    # ------------------------------------------------------------------
    # Discovery mode
    # ------------------------------------------------------------------

    def start_discovery(self, io_handler: Optional[Callable[[str], str]] = None) -> DiscoveryResult:
        """
        Start or resume discovery mode.

        Requires: plan does not exist OR status == DISCOVERY.

        Returns DiscoveryResult with brief and needs_approval=True.
        """
        plan = self._plan_repo.get()

        # Check if we can start discovery
        if plan is not None and plan.status != ProjectPlanStatus.DISCOVERY:
            return DiscoveryResult(
                session_id="",
                brief=None,
                needs_approval=False,
                failure_reason=f"Cannot start discovery: plan is in {plan.status.value} state",
            )

        # Find resumable session or create new one
        session = self._find_resumable_session(PlannerMode.DISCOVERY)
        if session is None:
            # Create new session with initial prompt
            prompt = "Describe the project vision and gather requirements through questions."
            session = PlannerSession.create(prompt, mode=PlannerMode.DISCOVERY)
            session.start()
            self._session_repo.save(session)

        # TODO: session resumption is incomplete — _build_resume_messages()
        # reconstructs conversation history, but PlannerRuntimePort.run_session()
        # does not accept a `messages` parameter.  Until the port interface is
        # extended, resumed sessions start a fresh LLM conversation.

        # Build tools for discovery mode
        tools = self._build_discovery_tools(session, io_handler)

        # Run interactive runtime
        try:
            output: PlannerOutput = self._interactive_runtime.run_session(
                prompt="Gather project requirements through interactive questions.",
                tools=tools,
                max_turns=20,
                session_callback=self._make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            log.error("planner_orchestrator.discovery_error", reason=str(exc))
            return DiscoveryResult(
                session_id=session.session_id,
                brief=None,
                needs_approval=False,
                failure_reason=str(exc),
            )

        # Parse brief from output (NOW WITH STRICT VALIDATION)
        try:
            brief = self._parse_brief(output.roadmap_raw)
        except ValueError as exc:
            session.fail(reason=f"Failed to parse brief: {exc}")
            self._session_repo.save(session)
            return DiscoveryResult(
                session_id=session.session_id,
                brief=None,
                needs_approval=False,
                failure_reason=f"LLM returned invalid schema: {exc}",
            )

        # Store brief in session before completing
        session.record_roadmap_candidate({"brief": output.roadmap_raw})

        self._session_repo.save(session)

        # Update session as completed
        session.complete(
            reasoning=output.reasoning,
            raw_llm_output=output.raw_text,
            validation_errors=[],
            validation_warnings=[],
        )
        self._session_repo.save(session)

        # Create plan if it doesn't exist
        if plan is None:
            plan = ProjectPlan.create(brief.vision)
            # FIX: We must attach the brief to the plan before saving it!
            plan = plan.model_copy(update={"brief": brief})
            self._plan_repo.save(plan)
        else:
            # If the plan already exists, we still need to update the brief on it
            plan = plan.model_copy(update={"brief": brief})
            self._plan_repo.save(plan)

        return DiscoveryResult(
            session_id=session.session_id,
            brief=brief,
            needs_approval=True,
        )

    def approve_brief(self) -> ProjectPlan:
        """
        Approve the project brief and transition to ARCHITECTURE mode.

        Requires: plan.status == DISCOVERY and plan.brief is not None.
        """
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.DISCOVERY:
            raise ValueError(f"Plan must be in DISCOVERY state, got {plan.status.value}")
        if plan.brief is None:
            raise ValueError("No brief to approve on the plan")

        plan = plan.approve_brief(plan.brief)
        self._plan_repo.save(plan)
        return plan

    # ------------------------------------------------------------------
    # Architecture mode
    # ------------------------------------------------------------------

    def run_architecture(
        self, io_handler: Optional[Callable[[str], str]] = None
    ) -> ArchitectureResult:
        """
        Run architecture planning mode.

        Requires: plan.status == ARCHITECTURE.
        """
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.ARCHITECTURE:
            return ArchitectureResult(
                session_id="",
                pending_decisions=[],
                pending_phases=[],
                needs_approval=False,
                failure_reason=f"Plan must be in ARCHITECTURE state, got {plan.status.value}",
            )

        # Find resumable session or create new one
        session = self._find_resumable_session(PlannerMode.ARCHITECTURE)
        if session is None:
            prompt = self._build_architecture_prompt()
            session = PlannerSession.create(prompt, mode=PlannerMode.ARCHITECTURE)
            session.start()
            self._session_repo.save(session)

        # Build tools for architecture mode
        tools = self._build_architecture_tools(session)

        # Run autonomous runtime
        try:
            output: PlannerOutput = self._autonomous_runtime.run_session(
                prompt=self._build_architecture_prompt(),
                tools=tools,
                max_turns=15,
                session_callback=self._make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            log.error("planner_orchestrator.architecture_error", reason=str(exc))
            return ArchitectureResult(
                session_id=session.session_id,
                pending_decisions=[],
                pending_phases=[],
                needs_approval=False,
                failure_reason=str(exc),
            )

        # Extract pending decisions and phases from session data
        pending_decisions = self._extract_pending_decisions(session)
        pending_phases = self._extract_pending_phases(session)

        # Update session as completed
        session.complete(
            reasoning=output.reasoning,
            raw_llm_output=output.raw_text,
            validation_errors=[],
            validation_warnings=[],
        )
        self._session_repo.save(session)

        return ArchitectureResult(
            session_id=session.session_id,
            pending_decisions=pending_decisions,
            pending_phases=pending_phases,
            needs_approval=True,
        )

    def approve_architecture(self, decision_ids: list[str]) -> ApprovalResult:
        """
        Approve selected decisions and transition to PHASE_ACTIVE.

        Requires: plan.status == ARCHITECTURE and an ARCHITECTURE session in COMPLETED state.
        """
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.ARCHITECTURE:
            raise ValueError(f"Plan must be in ARCHITECTURE state, got {plan.status.value}")

        # Find the completed ARCHITECTURE session
        sessions = self._session_repo.list_all()
        arch_session = None
        for s in sessions:
            if s.mode == PlannerMode.ARCHITECTURE and s.status == PlannerSessionStatus.COMPLETED:
                arch_session = s
                break

        if arch_session is None:
            raise ValueError("No completed ARCHITECTURE session found")

        # Extract pending decisions and phases from session
        pending_decisions = self._extract_pending_decisions(arch_session)
        pending_phases = self._extract_pending_phases(arch_session)

        # Apply approved decisions
        decisions_applied = 0
        spec_changes_applied = 0
        for entry in pending_decisions:
            if entry.id in decision_ids:
                # Write decision first (atomicity: decision written before spec change)
                self._project_state.write_decision(entry)
                decisions_applied += 1

                # Apply spec changes if present
                if entry.spec_changes and not entry.spec_changes.is_empty:
                    try:
                        changed = apply_decision_to_spec(
                            entry,
                            self._spec_repo,
                            self._project_name,
                        )
                        if changed:
                            spec_changes_applied += 1
                    except Exception as exc:
                        log.error(
                            "planner_orchestrator.spec_apply_failed",
                            decision_id=entry.id,
                            error=str(exc),
                        )

        # Approve the phase and transition to PHASE_ACTIVE
        plan = plan.approve_phase(pending_phases)
        self._plan_repo.save(plan)

        # Dispatch goals for Phase 1 in topological order
        goals_dispatched: list[str] = []
        if pending_phases:
            current_phase = pending_phases[0]
            for goal_name in current_phase.goal_names:
                # Find the goal spec in the session data
                goal_spec = self._find_goal_spec(arch_session, goal_name)
                if goal_spec:
                    try:
                        goal = self._goal_init.execute(goal_spec)
                        plan = plan.record_goal_registered(goal.name)
                        goals_dispatched.append(goal.goal_id)
                        # Phase 0 goals bypass UnblockGoalsUseCase, so emit
                        # goal.unblocked here to wake the Tactical JIT Planner.
                        if self._event_port is not None:
                            self._event_port.publish(DomainEvent(
                                type="goal.unblocked",
                                producer="planner-orchestrator",
                                payload={
                                    "goal_id": goal.goal_id,
                                    "name": goal.name,
                                    "feature_tag": goal.feature_tag,
                                },
                            ))
                    except Exception as exc:
                        log.error(
                            "planner_orchestrator.goal_dispatch_failed",
                            goal_name=goal_name,
                            error=str(exc),
                        )

        # Save updated plan after goal registration
        self._plan_repo.save(plan)

        return ApprovalResult(
            decisions_applied=decisions_applied,
            goals_dispatched=goals_dispatched,
            plan_status=plan.status.value,
            spec_changes_applied=spec_changes_applied,
        )

    # ------------------------------------------------------------------
    # Phase review mode
    # ------------------------------------------------------------------

    def run_phase_review(
        self, io_handler: Optional[Callable[[str], str]] = None
    ) -> PhaseReviewResult:
        """
        Run phase review mode.

        Requires: plan.status == PHASE_REVIEW.
        """
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_REVIEW:
            return PhaseReviewResult(
                session_id="",
                lessons="",
                next_phase_proposal=None,
                pending_decisions=[],
                needs_approval=False,
                failure_reason=f"Plan must be in PHASE_REVIEW state, got {plan.status.value}",
            )

        # Find resumable session or create new one
        session = self._find_resumable_session(PlannerMode.PHASE_REVIEW)
        if session is None:
            prompt = self._build_phase_review_prompt(plan)
            session = PlannerSession.create(prompt, mode=PlannerMode.PHASE_REVIEW)
            session.start()
            self._session_repo.save(session)

        # Build tools for phase review mode
        tools = self._build_phase_review_tools(session, plan)

        # Run autonomous runtime
        try:
            output: PlannerOutput = self._autonomous_runtime.run_session(
                prompt=self._build_phase_review_prompt(plan),
                tools=tools,
                max_turns=15,
                session_callback=self._make_session_callback(session),
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            log.error("planner_orchestrator.phase_review_error", reason=str(exc))
            return PhaseReviewResult(
                session_id=session.session_id,
                lessons="",
                next_phase_proposal=None,
                pending_decisions=[],
                needs_approval=False,
                failure_reason=str(exc),
            )

        # Extract results from session data
        lessons = self._extract_review_lessons(session)
        next_phase = self._extract_next_phase(session)
        pending_decisions = self._extract_pending_decisions(session)

        # Update session as completed
        session.complete(
            reasoning=output.reasoning,
            raw_llm_output=output.raw_text,
            validation_errors=[],
            validation_warnings=[],
        )
        self._session_repo.save(session)

        return PhaseReviewResult(
            session_id=session.session_id,
            lessons=lessons,
            next_phase_proposal=next_phase,
            pending_decisions=pending_decisions,
            needs_approval=True,
        )

    def approve_phase_review(self, approve_next: bool = True) -> ApprovalResult:
        """
        Approve the phase review and transition to next state.

        Requires: plan.status == PHASE_REVIEW and a PHASE_REVIEW session in COMPLETED state.
        """
        plan = self._plan_repo.load()
        if plan.status != ProjectPlanStatus.PHASE_REVIEW:
            raise ValueError(f"Plan must be in PHASE_REVIEW state, got {plan.status.value}")

        # Find the completed PHASE_REVIEW session
        sessions = self._session_repo.list_all()
        review_session = None
        for s in sessions:
            if s.mode == PlannerMode.PHASE_REVIEW and s.status == PlannerSessionStatus.COMPLETED:
                review_session = s
                break

        if review_session is None:
            raise ValueError("No completed PHASE_REVIEW session found")

        # Extract data from session
        lessons = self._extract_review_lessons(review_session)
        next_phase = self._extract_next_phase(review_session)
        pending_decisions = self._extract_pending_decisions(review_session)
        architecture_summary = self._extract_architecture_summary(review_session)

        # Complete the review
        plan = plan.complete_review(lessons, architecture_summary)

        # Apply pending decisions
        decisions_applied = 0
        for entry in pending_decisions:
            self._project_state.write_decision(entry)
            decisions_applied += 1
            if entry.spec_changes and not entry.spec_changes.is_empty:
                try:
                    apply_decision_to_spec(entry, self._spec_repo, self._project_name)
                except Exception as exc:
                    log.error(
                        "planner_orchestrator.spec_apply_failed",
                        decision_id=entry.id,
                        error=str(exc),
                    )

        goals_dispatched: list[str] = []

        if approve_next and next_phase:
            # Approve next phase and transition to PHASE_ACTIVE
            plan = plan.approve_phase([next_phase])
            self._plan_repo.save(plan)

            # Dispatch goals for the new phase
            for goal_name in next_phase.goal_names:
                goal_spec = self._find_goal_spec(review_session, goal_name)
                if goal_spec:
                    try:
                        goal = self._goal_init.execute(goal_spec)
                        plan = plan.record_goal_registered(goal.name)
                        goals_dispatched.append(goal.goal_id)
                    except Exception as exc:
                        log.error(
                            "planner_orchestrator.goal_dispatch_failed",
                            goal_name=goal_name,
                            error=str(exc),
                        )
        else:
            # Mark project as done
            plan = plan.mark_done()
            self._plan_repo.save(plan)

        # Save updated plan after goal registration
        self._plan_repo.save(plan)

        return ApprovalResult(
            decisions_applied=decisions_applied,
            goals_dispatched=goals_dispatched,
            plan_status=plan.status.value,
        )

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_status(self) -> ProjectPlan:
        """Return the current plan."""
        return self._plan_repo.load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_resumable_session(self, mode: PlannerMode) -> Optional[PlannerSession]:
        """Find an incomplete session for the given mode, newest first."""
        sessions = self._session_repo.list_all()
        for s in sessions:
            if s.mode == mode and s.status == PlannerSessionStatus.RUNNING:
                return s
        return None

    def _build_resume_messages(self, session: PlannerSession) -> list[dict]:
        """Reconstruct message history from turns[] for LLM context."""
        messages = []
        for turn in session.turns:
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def _make_session_callback(self, session: PlannerSession) -> Callable[[str, list[dict]], None]:
        """Create a session callback for persisting turns."""

        def callback(role: str, content_blocks: list[dict]) -> None:
            turn_index = len(session.turns)
            session.add_turn(role, content_blocks, turn_index)
            self._session_repo.save(session)

        return callback

    def _build_discovery_tools(
        self, session: PlannerSession, io_handler: Optional[Callable[[str], str]]
    ) -> list[PlannerTool]:
        """Build tools for DISCOVERY mode."""
        tools = []

        # ask_question tool (pauses for human input)
        def ask_question_handler(inp: dict) -> str:
            question = inp.get("question", "")
            # Note: The actual I/O happens in the runtime, not here
            return json.dumps({"asked": True, "question": question})

        tools.append(
            PlannerTool(
                name="ask_question",
                description="Ask the user a clarifying question about the project.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Question to ask the user"}
                    },
                    "required": ["question"],
                },
                handler=ask_question_handler,
            )
        )

        # submit_project_brief tool
        def submit_brief_handler(inp: dict) -> str:
            brief_json = inp.get("brief_json", "")
            try:
                data = json.loads(brief_json)

                # STRICT VALIDATION: Force the LLM to self-correct if it hallucinates keys
                required_keys = {"vision", "constraints", "phase_1_exit_criteria", "open_questions"}
                missing = required_keys - set(data.keys())
                if missing:
                    raise ValueError(
                        f"Missing required keys in JSON: {missing}. You must use the exact keys requested."
                    )

                # Store in session data for later extraction
                session.record_roadmap_candidate({"brief": data})
                self._session_repo.save(session)
                return json.dumps({"accepted": True})
            except Exception as exc:
                # Returning the error feeds it back to the LLM so it can try again
                return json.dumps({"accepted": False, "error": str(exc)})

        tools.append(
            PlannerTool(
                name="submit_project_brief",
                description="Submit the final project brief after gathering requirements.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "brief_json": {
                            "type": "string",
                            "description": (
                                "JSON string with brief data. MUST strictly follow this format: "
                                '{"vision": "High level summary", '
                                '"constraints": ["Limits"], '
                                '"phase_1_exit_criteria": "What defines MVP", '
                                '"open_questions": ["Pending items"]}'
                            ),
                        }
                    },
                    "required": ["brief_json"],
                },
                handler=submit_brief_handler,
            )
        )

        return tools

    def _build_architecture_tools(self, session: PlannerSession) -> list[PlannerTool]:
        """Build tools for ARCHITECTURE mode."""
        tools = []

        # read_project_brief tool
        def read_brief_handler(inp: dict) -> str:
            plan = self._plan_repo.get()
            if plan and plan.brief:
                brief = plan.brief
                return json.dumps(
                    {
                        "vision": brief.vision,
                        "constraints": brief.constraints,
                        "phase_1_exit_criteria": brief.phase_1_exit_criteria,
                        "open_questions": brief.open_questions,
                    }
                )
            return json.dumps({"error": "No brief found"})

        tools.append(
            PlannerTool(
                name="read_project_brief",
                description="Read the approved project brief.",
                input_schema={"type": "object", "properties": {}},
                handler=read_brief_handler,
            )
        )

        # propose_decision tool
        def propose_decision_handler(inp: dict) -> str:
            entry = DecisionEntry(
                id=inp["id"],
                date=inp.get("date", str(date.today())),
                status="active",
                domain=inp["domain"],
                feature_tag=inp.get("feature_tag", ""),
                content=inp["content"],
                spec_changes=self._parse_spec_changes(inp.get("spec_changes_json")),
            )
            # Store in session data for later extraction
            decisions = (
                session.roadmap_data.get("pending_decisions", []) if session.roadmap_data else []
            )
            decisions.append(entry.model_dump(mode="json"))
            session.record_roadmap_candidate({"pending_decisions": decisions})
            self._session_repo.save(session)
            return json.dumps({"proposed": True, "id": entry.id})

        tools.append(
            PlannerTool(
                name="propose_decision",
                description="Propose an architectural decision for approval.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short slug, e.g. 'use-fastapi'"},
                        "domain": {"type": "string", "description": "e.g. 'backend' or 'infra'"},
                        "feature_tag": {"type": "string"},
                        "content": {"type": "string", "description": "Markdown explanation"},
                        "spec_changes_json": {
                            "type": "string",
                            "description": (
                                "Optional JSON string for spec changes: "
                                '{"add_required": [], "add_forbidden": [], '
                                '"remove_required": [], "remove_forbidden": []}'
                            ),
                        },
                    },
                    "required": ["id", "domain", "content"],
                },
                handler=propose_decision_handler,
            )
        )

        # propose_phase_plan tool
        def propose_phase_plan_handler(inp: dict) -> str:
            phases_json = inp.get("phases_json", "[]")
            try:
                phases_data = json.loads(phases_json)
                if not isinstance(phases_data, list):
                    raise ValueError("phases_json must be a JSON array of objects.")

                # STRICT VALIDATION
                required = {"index", "name", "goal", "goal_names", "exit_criteria"}
                for i, phase in enumerate(phases_data):
                    missing = required - set(phase.keys())
                    if missing:
                        raise ValueError(f"Phase at index {i} is missing required keys: {missing}")

                # Store in session data for later extraction
                session.record_roadmap_candidate({"pending_phases": phases_data})
                self._session_repo.save(session)
                return json.dumps({"proposed": True, "phase_count": len(phases_data)})
            except Exception as exc:
                return json.dumps({"proposed": False, "error": str(exc)})

        tools.append(
            PlannerTool(
                name="propose_phase_plan",
                description="Propose the phase plan for approval.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "phases_json": {
                            "type": "string",
                            "description": (
                                "JSON string array of phases. Format: "
                                '[{"index": 0, "name": "Foundation", '
                                '"goal": "Setup base", "goal_names": ["setup-db"], '
                                '"exit_criteria": "DB is up"}]'
                            ),
                        }
                    },
                    "required": ["phases_json"],
                },
                handler=propose_phase_plan_handler,
            )
        )

        # submit_architecture tool
        def submit_architecture_handler(inp: dict) -> str:
            # Check that at least one decision and one phase were proposed
            data = session.roadmap_data or {}
            decisions = data.get("pending_decisions", [])
            phases = data.get("pending_phases", [])
            if not decisions:
                return json.dumps({"accepted": False, "error": "No decisions proposed"})
            if not phases:
                return json.dumps({"accepted": False, "error": "No phases proposed"})
            return json.dumps({"accepted": True})

        tools.append(
            PlannerTool(
                name="submit_architecture",
                description="Submit the architecture for approval. Requires at least one decision and one phase.",
                input_schema={"type": "object", "properties": {}},
                handler=submit_architecture_handler,
            )
        )

        return tools

    def _build_phase_review_tools(
        self, session: PlannerSession, plan: ProjectPlan
    ) -> list[PlannerTool]:
        """Build tools for PHASE_REVIEW mode."""
        tools = []

        # read_phase_summary tool
        def read_phase_summary_handler(inp: dict) -> str:
            current_phase = plan.current_phase()
            if not current_phase:
                return json.dumps({"error": "No active phase"})

            # Get goals in this phase
            goals = []
            for goal_name in current_phase.goal_names:
                goal = self._goal_repo.get_by_name(goal_name)
                if goal:
                    goals.append(
                        {
                            "name": goal.name,
                            "status": goal.status.value,
                            "description": goal.description,
                        }
                    )

            return json.dumps(
                {
                    "phase_name": current_phase.name,
                    "phase_goal": current_phase.goal,
                    "goals": goals,
                    "goal_names": current_phase.goal_names,
                }
            )

        tools.append(
            PlannerTool(
                name="read_phase_summary",
                description="Read summary of the completed phase.",
                input_schema={"type": "object", "properties": {}},
                handler=read_phase_summary_handler,
            )
        )

        # propose_decision tool (same as architecture mode)
        def propose_decision_handler(inp: dict) -> str:
            entry = DecisionEntry(
                id=inp["id"],
                date=inp.get("date", str(date.today())),
                status="active",
                domain=inp["domain"],
                feature_tag=inp.get("feature_tag", ""),
                content=inp["content"],
                spec_changes=self._parse_spec_changes(inp.get("spec_changes_json")),
            )
            decisions = (
                session.roadmap_data.get("pending_decisions", []) if session.roadmap_data else []
            )
            decisions.append(entry.model_dump(mode="json"))
            session.record_roadmap_candidate({"pending_decisions": decisions})
            self._session_repo.save(session)
            return json.dumps({"proposed": True, "id": entry.id})

        tools.append(
            PlannerTool(
                name="propose_decision",
                description="Propose an architectural decision for approval.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "domain": {"type": "string"},
                        "feature_tag": {"type": "string"},
                        "content": {"type": "string"},
                        "spec_changes_json": {"type": "string"},
                    },
                    "required": ["id", "domain", "content"],
                },
                handler=propose_decision_handler,
            )
        )

        # propose_next_phase tool
        def propose_next_phase_handler(inp: dict) -> str:
            phase_data = {
                "index": inp.get("index", plan.current_phase_index + 1),
                "name": inp.get("name", ""),
                "goal": inp.get("goal", ""),
                "exit_criteria": inp.get("exit_criteria", ""),
            }
            session.record_roadmap_candidate({"next_phase": phase_data})
            self._session_repo.save(session)
            return json.dumps({"proposed": True})

        tools.append(
            PlannerTool(
                name="propose_next_phase",
                description="Propose the next phase for approval.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "goal": {"type": "string"},
                        "exit_criteria": {"type": "string"},
                    },
                    "required": ["name", "goal", "exit_criteria"],
                },
                handler=propose_next_phase_handler,
            )
        )

        # submit_review tool
        def submit_review_handler(inp: dict) -> str:
            lessons = inp.get("lessons", "")
            architecture_summary = inp.get("architecture_summary", "")
            session.record_roadmap_candidate(
                {
                    "lessons": lessons,
                    "architecture_summary": architecture_summary,
                }
            )
            self._session_repo.save(session)
            return json.dumps({"accepted": True})

        tools.append(
            PlannerTool(
                name="submit_review",
                description="Submit the phase review with lessons and architecture update.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "lessons": {"type": "string"},
                        "architecture_summary": {"type": "string"},
                    },
                    "required": ["lessons", "architecture_summary"],
                },
                handler=submit_review_handler,
            )
        )

        return tools

    def _parse_brief(self, roadmap_raw: dict) -> ProjectBrief:
        """Extract ProjectBrief from roadmap_raw data."""
        brief_data = roadmap_raw.get("brief", roadmap_raw)  # Handle both formats

        vision = brief_data.get("vision")
        if not vision:
            raise ValueError(
                "The generated brief is missing a 'vision' statement. The LLM failed to generate the correct schema."
            )

        return ProjectBrief(
            vision=vision,
            constraints=brief_data.get("constraints", []),
            phase_1_exit_criteria=brief_data.get("phase_1_exit_criteria", ""),
            open_questions=brief_data.get("open_questions", []),
        )

    def _parse_spec_changes(self, spec_changes_json: Optional[str]) -> Optional[SpecChanges]:
        """Parse spec_changes JSON string into SpecChanges object."""
        if not spec_changes_json:
            return None
        try:
            data = json.loads(spec_changes_json)
            return SpecChanges(
                add_required=data.get("add_required", []),
                add_forbidden=data.get("add_forbidden", []),
                remove_required=data.get("remove_required", []),
                remove_forbidden=data.get("remove_forbidden", []),
            )
        except Exception:
            return None

    def _extract_pending_decisions(self, session: PlannerSession) -> list[DecisionEntry]:
        """Extract pending decisions from session roadmap_data."""
        if not session.roadmap_data:
            return []
        decisions_data = session.roadmap_data.get("pending_decisions", [])
        entries = []
        for d in decisions_data:
            sc = self._parse_spec_changes(d.get("spec_changes_json"))
            entries.append(
                DecisionEntry(
                    id=d["id"],
                    date=d.get("date", str(date.today())),
                    status=d.get("status", "active"),
                    domain=d["domain"],
                    feature_tag=d.get("feature_tag", ""),
                    content=d["content"],
                    spec_changes=sc,
                )
            )
        return entries

    def _extract_pending_phases(self, session: PlannerSession) -> list[Phase]:
        """Extract pending phases from session roadmap_data."""
        if not session.roadmap_data:
            return []
        phases_data = session.roadmap_data.get("pending_phases", [])
        phases = []
        for p in phases_data:
            phases.append(
                Phase(
                    index=p.get("index", 0),
                    name=p.get("name", ""),
                    goal=p.get("goal", ""),
                    goal_names=p.get("goal_names", []),
                    status=PhaseStatus.PLANNED,
                    lessons="",
                    exit_criteria=p.get("exit_criteria", ""),
                )
            )
        return phases

    def _extract_review_lessons(self, session: PlannerSession) -> str:
        """Extract lessons from session roadmap_data."""
        if not session.roadmap_data:
            return ""
        return session.roadmap_data.get("lessons", "")

    def _extract_next_phase(self, session: PlannerSession) -> Optional[Phase]:
        """Extract next phase proposal from session roadmap_data."""
        if not session.roadmap_data:
            return None
        phase_data = session.roadmap_data.get("next_phase")
        if not phase_data:
            return None
        return Phase(
            index=phase_data.get("index", 0),
            name=phase_data.get("name", ""),
            goal=phase_data.get("goal", ""),
            goal_names=[],  # Will be populated when dispatched
            status=PhaseStatus.PLANNED,
            lessons="",
            exit_criteria=phase_data.get("exit_criteria", ""),
        )

    def _extract_architecture_summary(self, session: PlannerSession) -> str:
        """Extract architecture summary from session roadmap_data."""
        if not session.roadmap_data:
            return ""
        return session.roadmap_data.get("architecture_summary", "")

    def _find_goal_spec(self, session: PlannerSession, goal_name: str) -> Any:
        """
        Build a task-less GoalSpec for a goal that appears in the session's
        ``pending_phases`` roadmap data.

        The Strategic Planner (Tier 1) only produces phase/goal *names* and
        descriptions — tasks are left for the Tactical JIT Planner.  We
        therefore construct a ``GoalSpec`` with an empty ``tasks`` list so
        that ``GoalInitUseCase`` can create the branch and aggregate without
        waiting for task details.
        """
        from src.domain.value_objects.goal import GoalSpec

        if not session.roadmap_data:
            return None

        # Search every proposed phase for the matching goal name.
        for phase_data in session.roadmap_data.get("pending_phases", []):
            if goal_name not in phase_data.get("goal_names", []):
                continue

            # Use the phase-level "goal" field as the per-goal description when
            # a finer-grained description hasn't been stored.
            description = phase_data.get("goal", f"Goal: {goal_name}")

            # Also check whether per-goal metadata was stored under
            # "goal_descriptions" (future extension point).
            goal_descs: dict = session.roadmap_data.get("goal_descriptions", {})
            if goal_name in goal_descs:
                description = goal_descs[goal_name]

            try:
                return GoalSpec(
                    name=goal_name,
                    description=description,
                    tasks=[],  # filled by PlanGoalTasksUseCase at execution time
                )
            except Exception as exc:
                log.warning(
                    "planner_orchestrator.goal_spec_build_failed",
                    goal_name=goal_name,
                    error=str(exc),
                )
                return None

        log.warning(
            "planner_orchestrator.goal_name_not_in_phases",
            goal_name=goal_name,
        )
        return None

    def _build_architecture_prompt(self) -> str:
        """Build prompt for architecture mode."""
        ctx = self._context_assembler.assemble()
        return (
            f"{ctx.to_prompt_context()}\n\n"
            "---\n\n"
            "## Architecture Planning Request\n\n"
            "Propose architectural decisions and a phase plan for the project.\n\n"
            "1. Use `read_project_brief` to see the project brief\n"
            "2. Use `propose_decision` to propose decisions (can be called multiple times)\n"
            "3. Use `propose_phase_plan` to propose the initial phase(s)\n"
            "4. Use `submit_architecture` when ready for approval"
        )

    def _build_phase_review_prompt(self, plan: ProjectPlan) -> str:
        """Build prompt for phase review mode."""
        ctx = self._context_assembler.assemble()
        return (
            f"{ctx.to_prompt_context()}\n\n"
            "---\n\n"
            "## Phase Review Request\n\n"
            f"Review the completed phase (index {plan.current_phase_index}) and plan the next phase.\n\n"
            "1. Use `read_phase_summary` to see what was built\n"
            "2. Use `propose_decision` for any new architectural decisions\n"
            "3. Use `propose_next_phase` to define the next phase\n"
            "4. Use `submit_review` with lessons learned and architecture updates"
        )
