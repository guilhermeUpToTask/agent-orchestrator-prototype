from __future__ import annotations

from typing import Any, Callable, Optional

import structlog

from src.app.planning.parsing.brief_parser import BriefParseError, BriefParser
from src.app.planning.parsing.decisions_parser import DecisionsParseError, DecisionsParser
from src.app.planning.parsing.phase_parser import PhaseParseError, PhaseParser
from src.app.planning.parsing.review_parser import ReviewParser
from src.app.planning.parsing.spec_changes_parser import SpecChangesParser
from src.app.planning.prompts.planning_prompt_builders import (
    ArchitecturePromptBuilder,
    DiscoveryPromptBuilder,
    PhaseReviewPromptBuilder,
)
from src.app.planning.tools.architecture_tools import (
    build_propose_phase_plan_tool,
    build_read_project_brief_tool,
    build_submit_architecture_tool,
)
from src.app.planning.tools.decision_tools import build_propose_decision_tool
from src.app.planning.tools.discovery_tools import build_ask_question_tool, build_submit_project_brief_tool
from src.app.planning.tools.phase_review_tools import (
    build_propose_next_phase_tool,
    build_read_phase_summary_tool,
    build_submit_review_tool,
)
from src.app.services.planner_context import PlannerContextAssembler, PlanningContextRenderer
from src.domain.aggregates.planner_session import PlannerMode, PlannerSession, PlannerSessionStatus
from src.domain.aggregates.project_plan import Phase, ProjectBrief, ProjectPlan
from src.domain.ports.planner import PlannerTool
from src.domain.ports.project_state import DecisionEntry
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.planner_session_repository import PlannerSessionRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort

log = structlog.get_logger(__name__)


class PlanningSessionSupport:
    def __init__(
        self,
        context_assembler: PlannerContextAssembler,
        session_repo: PlannerSessionRepositoryPort,
        plan_repo: ProjectPlanRepositoryPort,
        goal_repo: GoalRepositoryPort,
    ) -> None:
        self._context_assembler = context_assembler
        self._session_repo = session_repo
        self._plan_repo = plan_repo
        self._goal_repo = goal_repo

        self._turn_callback: Optional[Callable[[str, list], None]] = None
        self._planner_event_hook: Optional[Callable[[str, dict], None]] = None

        spec_changes_parser = SpecChangesParser()
        self._brief_parser = BriefParser()
        self._decisions_parser = DecisionsParser(spec_changes_parser)
        self._phase_parser = PhaseParser()
        self._review_parser = ReviewParser()

        renderer = PlanningContextRenderer()
        self._discovery_prompt_builder = DiscoveryPromptBuilder(renderer=renderer)
        self._architecture_prompt_builder = ArchitecturePromptBuilder(renderer=renderer)
        self._phase_review_prompt_builder = PhaseReviewPromptBuilder(renderer=renderer)
        self._spec_changes_parser = spec_changes_parser

    def set_turn_callback(self, callback: Optional[Callable[[str, list], None]]) -> None:
        self._turn_callback = callback

    def set_planner_event_hook(self, hook: Optional[Callable[[str, dict], None]]) -> None:
        self._planner_event_hook = hook

    def find_resumable_session(self, mode: PlannerMode) -> Optional[PlannerSession]:
        sessions = self._session_repo.list_all()
        for s in sessions:
            if s.mode == mode and s.status == PlannerSessionStatus.RUNNING:
                return s
        return None

    def make_session_callback(self, session: PlannerSession) -> Callable[[str, list], None]:
        def callback(role: str, content_blocks: list) -> None:
            turn_index = len(session.turns)
            session.add_turn(role, content_blocks, turn_index)
            self._session_repo.save(session)
            if self._turn_callback is not None:
                self._turn_callback(role, content_blocks)

        return callback

    def build_discovery_tools(
        self, session: PlannerSession, io_handler: Optional[Callable[[str], str]]
    ) -> list[PlannerTool]:
        _ = io_handler
        return [
            build_ask_question_tool(),
            build_submit_project_brief_tool(session=session, session_save=self._session_repo.save),
        ]

    def build_architecture_tools(self, session: PlannerSession) -> list[PlannerTool]:
        return [
            build_read_project_brief_tool(self._plan_repo),
            build_propose_decision_tool(
                session=session,
                session_save=self._session_repo.save,
                spec_changes_parser=self._spec_changes_parser,
                event_hook=self._planner_event_hook,
                strict_schema=True,
            ),
            build_propose_phase_plan_tool(
                session=session,
                session_save=self._session_repo.save,
                event_hook=self._planner_event_hook,
            ),
            build_submit_architecture_tool(session),
        ]

    def build_phase_review_tools(self, session: PlannerSession, plan: ProjectPlan) -> list[PlannerTool]:
        return [
            build_read_phase_summary_tool(plan=plan, goal_repo=self._goal_repo),
            build_propose_decision_tool(
                session=session,
                session_save=self._session_repo.save,
                spec_changes_parser=self._spec_changes_parser,
                strict_schema=False,
            ),
            build_propose_next_phase_tool(
                session=session,
                session_save=self._session_repo.save,
                default_index=plan.current_phase_index + 1,
            ),
            build_submit_review_tool(session=session, session_save=self._session_repo.save),
        ]

    def parse_brief(self, roadmap_raw: dict) -> ProjectBrief:
        try:
            return self._brief_parser.parse(roadmap_raw)
        except BriefParseError as exc:
            raise ValueError(str(exc)) from exc

    def extract_pending_decisions(self, session: PlannerSession) -> list[DecisionEntry]:
        try:
            return self._decisions_parser.parse_pending(session.roadmap_data)
        except DecisionsParseError as exc:
            log.warning("planner_orchestrator.pending_decisions_parse_failed", error=str(exc))
            return []

    def extract_pending_phases(self, session: PlannerSession) -> list[Phase]:
        try:
            return self._phase_parser.parse_pending(session.roadmap_data)
        except PhaseParseError as exc:
            log.warning("planner_orchestrator.pending_phases_parse_failed", error=str(exc))
            return []

    def extract_review_lessons(self, session: PlannerSession) -> str:
        return self._review_parser.parse_lessons(session.roadmap_data)

    def extract_next_phase(self, session: PlannerSession) -> Optional[Phase]:
        try:
            return self._phase_parser.parse_next(session.roadmap_data)
        except PhaseParseError as exc:
            log.warning("planner_orchestrator.next_phase_parse_failed", error=str(exc))
            return None

    def extract_architecture_summary(self, session: PlannerSession) -> str:
        return self._review_parser.parse_architecture_summary(session.roadmap_data)

    def find_goal_spec(self, session: PlannerSession, goal_name: str) -> Any:
        from src.domain.value_objects.goal import GoalSpec

        if not session.roadmap_data:
            return None

        for phase_data in session.roadmap_data.get("pending_phases", []):
            if goal_name not in phase_data.get("goal_names", []):
                continue

            description = phase_data.get("goal", f"Goal: {goal_name}")
            goal_descs: dict = session.roadmap_data.get("goal_descriptions", {})
            if goal_name in goal_descs:
                description = goal_descs[goal_name]

            try:
                return GoalSpec(name=goal_name, description=description, tasks=[])
            except Exception as exc:
                log.warning(
                    "planner_orchestrator.goal_spec_build_failed",
                    goal_name=goal_name,
                    error=str(exc),
                )
                return None

        log.warning("planner_orchestrator.goal_name_not_in_phases", goal_name=goal_name)
        return None

    def build_architecture_prompt(self) -> str:
        return self._architecture_prompt_builder.build(self._context_assembler.assemble())

    def build_discovery_prompt(self) -> str:
        return self._discovery_prompt_builder.build(self._context_assembler.assemble())

    def build_phase_review_prompt(self, plan: ProjectPlan) -> str:
        return self._phase_review_prompt_builder.build(self._context_assembler.assemble(), plan)
