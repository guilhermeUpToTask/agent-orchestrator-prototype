"""
src/app/usecases/run_planning_session.py — RunPlanningSessionUseCase.

Orchestrates the full planning pipeline:
  1. Create PlannerSession + start()
  2. Assemble PlannerContext
  3. Build prompt
  4. Define five tools (validate_roadmap, check_capability, list_active_goals,
     write_decision, submit_final_roadmap)
  5. Run agentic loop via PlannerRuntimePort
  6. Validate output + capability check
  7. complete() / fail() session
  8. Optionally dispatch goals in topological order
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import structlog

from src.app.services.planner_context import PlannerContextAssembler
from src.app.usecases.goal_init import GoalInitUseCase
from src.app.usecases.validate_against_spec import ValidateAgainstSpec
from src.domain.aggregates.planner_session import PlannerSession
from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)
from src.domain.ports.project_state import DecisionEntry, ProjectStatePort
from src.domain.repositories.agent_registry import AgentRegistryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.planner_session_repository import (
    PlannerSessionRepositoryPort,
)
from src.domain.value_objects.goal import GoalSpec, GoalTaskDef, Roadmap

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 5


@dataclass
class PlanningResult:
    session_id: str
    roadmap: Optional[Roadmap] = None
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
    """
    Orchestration core for the planning layer.

    dispatch=False is the default — the planner never dispatches goals
    without explicit confirmation.  Use dispatch_roadmap(session_id) after
    the human confirms the plan.
    """

    def __init__(
        self,
        context_assembler: PlannerContextAssembler,
        planner_runtime: PlannerRuntimePort,
        session_repo: PlannerSessionRepositoryPort,
        goal_init: GoalInitUseCase,
        validator: ValidateAgainstSpec,
        project_state: ProjectStatePort,
        agent_registry: AgentRegistryPort,
        goal_repo: Optional[GoalRepositoryPort] = None,
    ) -> None:
        import warnings
        warnings.warn(
            "RunPlanningSessionUseCase is deprecated; "
            "use PlannerOrchestrator instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._context_assembler = context_assembler
        self._runtime = planner_runtime
        self._session_repo = session_repo
        self._goal_init = goal_init
        self._validator = validator
        self._project_state = project_state
        self._agent_registry = agent_registry
        self._goal_repo = goal_repo  # needed for list_active_goals tool + dispatch

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def execute(self, user_input: str, dispatch: bool = False) -> PlanningResult:
        """
        Run the full planning pipeline for *user_input*.

        Returns PlanningResult.  If dispatch=True and no validation errors,
        goals are dispatched immediately in topological order.
        """
        session = PlannerSession.create(user_input)
        session.start()
        self._session_repo.save(session)

        # Assemble context
        ctx = self._context_assembler.assemble()
        prompt = _build_prompt(user_input, ctx.to_prompt_context())

        # Build tools — closures capture session + ports
        tools = self._build_tools(session)

        # session_callback: persist each turn as it arrives
        def session_callback(role: str, content_blocks: list[dict]) -> None:
            turn_index = len(session.turns)
            session.add_turn(role, content_blocks, turn_index)
            self._session_repo.save(session)

        # Run agentic loop
        try:
            output: PlannerOutput = self._runtime.run_session(
                prompt=prompt,
                tools=tools,
                max_turns=15,
                session_callback=session_callback,
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            log.error("run_planning_session.runtime_error", reason=str(exc))
            return PlanningResult(
                session_id=session.session_id,
                failure_reason=str(exc),
            )

        # Parse roadmap from output
        try:
            roadmap, parse_errors = self._parse_and_validate_roadmap(
                output.roadmap_raw
            )
        except Exception as exc:
            reason = f"Roadmap parse failed: {exc}"
            session.fail(reason=reason, raw_llm_output=output.raw_text)
            self._session_repo.save(session)
            return PlanningResult(
                session_id=session.session_id,
                failure_reason=reason,
            )

        # Capability validation
        cap_errors = self._validate_capabilities(roadmap) if roadmap else []
        all_errors = parse_errors + cap_errors

        # Spec validation per goal
        spec_warnings: list[str] = []
        if roadmap:
            for spec in roadmap.goals:
                for task in spec.tasks:
                    result = self._validator.execute(
                        task_description=task.description,
                    )
                    all_errors.extend(result.violations)
                    spec_warnings.extend(result.warnings)

        session.complete(
            reasoning=output.reasoning,
            raw_llm_output=output.raw_text,
            validation_errors=all_errors,
            validation_warnings=spec_warnings + output.arch_update.split("\n") if output.arch_update else spec_warnings,
        )
        self._session_repo.save(session)

        # Persist arch/decisions updates
        if output.arch_update:
            self._project_state.write_state("current_arch", output.arch_update)

        result = PlanningResult(
            session_id=session.session_id,
            roadmap=roadmap,
            validation_errors=all_errors,
            validation_warnings=spec_warnings,
        )

        if dispatch and roadmap and not all_errors:
            dispatched = self._dispatch_goals(session, roadmap)
            result.goals_dispatched = dispatched

        return result

    # ------------------------------------------------------------------
    # Dispatch roadmap (separate step — human confirms first)
    # ------------------------------------------------------------------

    def dispatch_roadmap(self, session_id: str) -> PlanningResult:
        """
        Re-load a completed session and dispatch its roadmap.  Idempotent.
        """
        session = self._session_repo.load(session_id)
        if not session.has_valid_roadmap():
            raise ValueError(
                f"Session '{session_id}' does not have a valid roadmap. "
                f"Status={session.status.value}, errors={session.validation_errors}"
            )
        roadmap, errors = self._parse_and_validate_roadmap(session.roadmap_data)
        if errors or not roadmap:
            raise ValueError(f"Roadmap re-parse failed: {errors}")

        dispatched = self._dispatch_goals(session, roadmap)
        return PlanningResult(
            session_id=session_id,
            roadmap=roadmap,
            goals_dispatched=dispatched,
        )

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def _build_tools(self, session: PlannerSession) -> list[PlannerTool]:
        return [
            self._tool_validate_roadmap(),
            self._tool_check_capability(),
            self._tool_list_active_goals(),
            self._tool_write_decision(session),
            self._tool_submit_final_roadmap(session),
        ]

    def _tool_validate_roadmap(self) -> PlannerTool:
        def handler(inp: dict) -> str:
            raw = inp.get("roadmap_json", "")
            try:
                data = _parse_json_safe(raw)
                _parse_goal_specs(data)
                Roadmap(goals=_parse_goal_specs(data))
                return json.dumps({"valid": True})
            except Exception as exc:
                return json.dumps({"valid": False, "error": str(exc)})

        return PlannerTool(
            name="validate_roadmap",
            description=(
                "Validate a roadmap JSON before submission. Returns {valid: true} "
                "or {valid: false, error: '<details>'}. Use this to self-correct "
                "before calling submit_final_roadmap."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "roadmap_json": {
                        "type": "string",
                        "description": "JSON string of the roadmap {goals: [...]}",
                    }
                },
                "required": ["roadmap_json"],
            },
            handler=handler,
        )

    def _tool_check_capability(self) -> PlannerTool:
        def handler(inp: dict) -> str:
            capability = inp.get("capability", "")
            agents = self._agent_registry.list_agents()
            matching = [a for a in agents if capability in a.capabilities]
            if matching:
                return json.dumps({
                    "exists": True,
                    "agents": [a.name for a in matching],
                })
            available = sorted({c for a in agents for c in a.capabilities})
            return json.dumps({"exists": False, "available": available})

        return PlannerTool(
            name="check_capability",
            description=(
                "Check if any registered agent has a given capability. "
                "Returns {exists: true, agents: [...]} or {exists: false, available: [...]}."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "description": "Capability string to check, e.g. 'python', 'frontend'",
                    }
                },
                "required": ["capability"],
            },
            handler=handler,
        )

    def _tool_list_active_goals(self) -> PlannerTool:
        def handler(inp: dict) -> str:
            if not self._goal_repo:
                return json.dumps({"goals": [], "note": "Goal repository not available"})
            goals = self._goal_repo.list_all()
            active = [
                {
                    "name": g.name,
                    "status": g.status.value,
                    "description": g.description[:120],
                }
                for g in goals
                if not g.is_terminal()
            ]
            return json.dumps({"goals": active, "count": len(active)})

        return PlannerTool(
            name="list_active_goals",
            description=(
                "List all non-terminal goals currently in the system. "
                "Use this to avoid re-planning work that is already underway."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )

    def _tool_write_decision(self, session: PlannerSession) -> PlannerTool:
        def handler(inp: dict) -> str:
            try:
                entry = DecisionEntry(
                    id=inp["id"],
                    date=inp.get("date", str(date.today())),
                    status="active",
                    domain=inp["domain"],
                    feature_tag=inp.get("feature_tag", ""),
                    content=inp["content"],
                )
                self._project_state.write_decision(entry)
                log.info(
                    "run_planning_session.decision_written",
                    session_id=session.session_id,
                    decision_id=entry.id,
                )
                return json.dumps({"written": True, "id": entry.id})
            except Exception as exc:
                return json.dumps({"written": False, "error": str(exc)})

        return PlannerTool(
            name="write_decision",
            description=(
                "Persist an architectural decision immediately. "
                "Decisions survive even if the planning session is later abandoned."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Slug ID, e.g. 'jwt-auth'"},
                    "domain": {"type": "string", "description": "Domain, e.g. 'authentication'"},
                    "feature_tag": {"type": "string", "description": "Feature tag or empty"},
                    "content": {"type": "string", "description": "Full markdown decision body"},
                    "date": {"type": "string", "description": "ISO date, defaults to today"},
                },
                "required": ["id", "domain", "content"],
            },
            handler=handler,
        )

    def _tool_submit_final_roadmap(self, session: PlannerSession) -> PlannerTool:
        def handler(inp: dict) -> str:
            raw = inp.get("roadmap_json", "")
            try:
                data = _parse_json_safe(raw)
                specs = _parse_goal_specs(data)
                Roadmap(goals=specs)  # validate — raises on error
                session.record_roadmap_candidate(data)
                self._session_repo.save(session)
                log.info(
                    "run_planning_session.roadmap_submitted",
                    session_id=session.session_id,
                    goal_count=len(specs),
                )
                return json.dumps({"accepted": True, "goal_count": len(specs)})
            except Exception as exc:
                return json.dumps({"accepted": False, "error": str(exc)})

        return PlannerTool(
            name="submit_final_roadmap",
            description=(
                "Submit the final validated roadmap. This is the ONLY valid exit "
                "condition. The roadmap must pass Roadmap validation before this "
                "returns accepted=true. Call validate_roadmap first if unsure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "roadmap_json": {
                        "type": "string",
                        "description": "JSON string {goals: [{name, description, tasks: [...], depends_on, feature_tag}]}",
                    }
                },
                "required": ["roadmap_json"],
            },
            handler=handler,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_and_validate_roadmap(
        self, roadmap_data: Optional[dict]
    ) -> tuple[Optional[Roadmap], list[str]]:
        if not roadmap_data:
            return None, ["No roadmap data found in session"]
        try:
            specs = _parse_goal_specs(roadmap_data)
            roadmap = Roadmap(goals=specs)
            return roadmap, []
        except Exception as exc:
            return None, [str(exc)]

    def _validate_capabilities(self, roadmap: Roadmap) -> list[str]:
        """Check every task capability against the live agent registry."""
        agents = self._agent_registry.list_agents()
        all_caps = {c for a in agents for c in a.capabilities}
        errors: list[str] = []

        if not agents:
            # Empty registry → warnings only (test environments)
            return []

        for spec in roadmap.goals:
            for task in spec.tasks:
                if task.capability not in all_caps:
                    errors.append(
                        f"Goal '{spec.name}', task '{task.task_id}': "
                        f"capability '{task.capability}' not found in registry. "
                        f"Available: {sorted(all_caps)}"
                    )
        return errors

    def _dispatch_goals(
        self, session: PlannerSession, roadmap: Roadmap
    ) -> list[str]:
        """Dispatch goals in topological order. Idempotent."""
        # Build set of already-dispatched goal names from history
        already_dispatched: set[str] = set()
        for entry in session.history:
            if entry.event == "planner.goal_dispatched":
                name = entry.detail.get("goal_name")
                if name:
                    already_dispatched.add(name)

        dispatched_ids: list[str] = []
        for spec in roadmap.topological_order():
            if spec.name in already_dispatched:
                log.info(
                    "run_planning_session.goal_already_dispatched",
                    goal_name=spec.name,
                )
                continue
            try:
                goal = self._goal_init.execute(spec)
                session.record_goal_dispatched(goal.goal_id, spec.name)
                self._session_repo.save(session)
                dispatched_ids.append(goal.goal_id)
                log.info(
                    "run_planning_session.goal_dispatched",
                    goal_name=spec.name,
                    goal_id=goal.goal_id,
                )
            except Exception as exc:
                session.record_dispatch_failure(spec.name, str(exc))
                self._session_repo.save(session)
                log.error(
                    "run_planning_session.dispatch_failed",
                    goal_name=spec.name,
                    error=str(exc),
                )

        return dispatched_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_prompt(user_input: str, context_str: str) -> str:
    return (
        f"{context_str}\n\n"
        "---\n\n"
        "## Planning request\n\n"
        f"{user_input}\n\n"
        "---\n\n"
        "Plan a roadmap of goals to fulfil the request. Each goal must have at least "
        "one task. Use validate_roadmap to check your plan before calling "
        "submit_final_roadmap. submit_final_roadmap is the only valid exit."
    )


def _parse_json_safe(raw: str) -> dict:
    """Parse JSON, stripping markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting first {...} block
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise


def _parse_goal_specs(data: dict) -> list[GoalSpec]:
    """Convert raw roadmap dict to list[GoalSpec]."""
    goals_raw = data.get("goals", [])
    if not isinstance(goals_raw, list):
        raise ValueError("Roadmap must have a 'goals' list")
    specs: list[GoalSpec] = []
    for g in goals_raw:
        tasks = [GoalTaskDef(**t) for t in g.get("tasks", [])]
        spec = GoalSpec(
            goal_id=g.get("goal_id"),
            name=g["name"],
            description=g["description"],
            tasks=tasks,
            depends_on=g.get("depends_on", []),
            feature_tag=g.get("feature_tag"),
        )
        specs.append(spec)
    return specs
