"""
src/app/usecases/run_refinement.py — AIPOM tactical refinement use case.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

import structlog

from src.app.services.planner_context import PlannerContextAssembler
from src.app.services.task_creation import TaskCreationService
from src.domain.aggregates.planner_session import PlannerMode, PlannerSession
from src.domain.ports.messaging import EventPort
from src.domain.ports.planner import PlannerRuntimeError, PlannerRuntimePort, PlannerTool
from src.domain.repositories.agent_registry import AgentRegistryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.planner_session_repository import PlannerSessionRepositoryPort
from src.domain.repositories.task_repository import TaskRepositoryPort

log = structlog.get_logger(__name__)

_MUTABLE_STATUSES = {"created", "assigned"}
_SUCCESSFUL_DEPENDENCY_STATUSES = {"succeeded", "merged"}


@dataclass
class RefinementResult:
    session_id: str
    actions_taken: list[str] = field(default_factory=list)
    succeeded: bool = True
    error: Optional[str] = None


class RunRefinementUseCase:
    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        task_repo: TaskRepositoryPort,
        task_creation: TaskCreationService,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        planner_runtime: PlannerRuntimePort,
        context_assembler: PlannerContextAssembler,
        session_repo: PlannerSessionRepositoryPort,
    ) -> None:
        self._goal_repo = goal_repo
        self._task_repo = task_repo
        self._task_creation = task_creation
        self._agent_registry = agent_registry
        self._event_port = event_port
        self._runtime = planner_runtime
        self._context_assembler = context_assembler
        self._session_repo = session_repo

    def execute(
        self,
        user_message: str,
        focused_node_id: Optional[str] = None,
        focused_goal_id: Optional[str] = None,
    ) -> RefinementResult:
        session_id = f"refine-{uuid4().hex[:8]}"
        actions: list[str] = []

        try:
            self._context_assembler.assemble()
        except Exception as exc:
            log.error("run_refinement.context_failed", error=str(exc))
            return RefinementResult(
                session_id=session_id,
                succeeded=False,
                error=f"Context assembly failed: {exc}",
            )

        focused_task_info = self._focused_task_info(focused_node_id, focused_goal_id)
        tools = self._build_tools(actions)
        prompt = self._build_prompt(user_message, focused_task_info)

        session = PlannerSession.create(user_message, mode=PlannerMode.TACTICAL)
        session.start()
        self._session_repo.save(session)

        try:
            self._runtime.run_session(
                prompt=prompt,
                tools=tools,
                max_turns=5,
                session_callback=lambda _sid, _turns: None,
            )
        except PlannerRuntimeError as exc:
            session.fail(reason=str(exc))
            self._session_repo.save(session)
            log.error("run_refinement.runtime_error", error=str(exc))
            return RefinementResult(
                session_id=session.session_id,
                actions_taken=actions,
                succeeded=False,
                error=str(exc),
            )

        session.record_roadmap_candidate({"mode": PlannerMode.TACTICAL.value, "actions_taken": actions})
        session.complete(
            reasoning="Tactical refinement session",
            raw_llm_output="",
            validation_errors=[],
            validation_warnings=[],
        )
        self._session_repo.save(session)

        return RefinementResult(
            session_id=session.session_id,
            actions_taken=actions if actions else ["No changes made — request was informational."],
            succeeded=True,
        )

    def _focused_task_info(
        self,
        focused_node_id: Optional[str],
        focused_goal_id: Optional[str],
    ) -> Optional[dict]:
        if not focused_node_id:
            return None
        try:
            task = self._task_repo.load(focused_node_id)
            goal_id = focused_goal_id
            if not goal_id:
                for goal in self._goal_repo.list_all():
                    if focused_node_id in goal.tasks:
                        goal_id = goal.goal_id
                        break
            return {
                "task_id": focused_node_id,
                "title": task.title,
                "status": task.status.value,
                "goal_id": goal_id,
                "capability": task.agent_selector.required_capability,
                "description": task.description,
            }
        except Exception as exc:
            log.warning("run_refinement.focused_task_unavailable", task_id=focused_node_id, error=str(exc))
            return None

    def _build_tools(self, actions: list[str]) -> list[PlannerTool]:
        return [
            self._update_task_tool(actions),
            self._reassign_agent_tool(actions),
            self._add_task_tool(actions),
            self._explain_task_tool(),
        ]

    def _update_task_tool(self, actions: list[str]) -> PlannerTool:
        task_repo, event_port = self._task_repo, self._event_port

        def handler(inp: dict) -> str:
            task_id = inp.get("task_id", "")
            field_name = inp.get("field", "")
            new_value = inp.get("value", "")
            allowed_fields = {
                "title",
                "description",
                "capability",
                "acceptance_criteria",
                "files_allowed_to_modify",
            }
            if field_name not in allowed_fields:
                return json.dumps({"ok": False, "error": f"Field '{field_name}' is not editable."})

            try:
                task = task_repo.load(task_id)
            except Exception:
                return json.dumps({"ok": False, "error": f"Task {task_id} not found."})

            if task.status.value not in _MUTABLE_STATUSES:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Task {task_id} is in '{task.status.value}' — cannot modify.",
                    }
                )

            if field_name in ("title", "description"):
                updated = task.model_copy(update={field_name: new_value})
            elif field_name == "capability":
                updated = task.model_copy(
                    update={
                        "agent_selector": task.agent_selector.model_copy(
                            update={"required_capability": new_value}
                        )
                    }
                )
            else:
                list_value = self._coerce_list_value(new_value)
                updated = task.model_copy(
                    update={"execution": task.execution.model_copy(update={field_name: list_value})}
                )
            task_repo.save(updated)
            self._publish_task_updated({"task_id": task_id, "field": field_name, "actor": "aipom"}, event_port)

            actions.append(f"{task_id}: `{field_name}` updated")
            return json.dumps({"ok": True, "task_id": task_id, "field": field_name})

        return PlannerTool(
            name="update_task",
            description="Update a single field of a task that is in CREATED or ASSIGNED status.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID of the task to update"},
                    "field": {
                        "type": "string",
                        "enum": [
                            "title",
                            "description",
                            "capability",
                            "acceptance_criteria",
                            "files_allowed_to_modify",
                        ],
                    },
                    "value": {"description": "New value. For list fields, pass a JSON array string."},
                },
                "required": ["task_id", "field", "value"],
            },
            handler=handler,
        )

    def _reassign_agent_tool(self, actions: list[str]) -> PlannerTool:
        task_repo, agent_registry, event_port = self._task_repo, self._agent_registry, self._event_port

        def handler(inp: dict) -> str:
            task_id = inp.get("task_id", "")
            agent_name = inp.get("agent_name", "")
            agents = agent_registry.list_agents()
            agent = next((a for a in agents if a.name == agent_name), None)
            if agent is None:
                return json.dumps({"ok": False, "error": f"Agent '{agent_name}' not found."})

            try:
                task = task_repo.load(task_id)
            except Exception:
                return json.dumps({"ok": False, "error": f"Task {task_id} not found."})

            if task.status.value not in _MUTABLE_STATUSES:
                return json.dumps({"ok": False, "error": f"Task {task_id} is '{task.status.value}'."})

            if task.agent_selector.required_capability not in agent.capabilities:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Agent lacks required capability "
                        f"'{task.agent_selector.required_capability}'.",
                    }
                )

            updated_constraints = dict(task.execution.constraints or {})
            updated_constraints["preferred_agent"] = agent.agent_id
            updated = task.model_copy(
                update={"execution": task.execution.model_copy(update={"constraints": updated_constraints})}
            )
            task_repo.save(updated)
            self._publish_task_updated({"task_id": task_id, "preferred_agent": agent.agent_id}, event_port)

            actions.append(f"{task_id}: preferred agent → `{agent_name}`")
            return json.dumps({"ok": True, "task_id": task_id, "agent_id": agent.agent_id})

        return PlannerTool(
            name="reassign_agent",
            description="Change the preferred agent for a task in CREATED or ASSIGNED status.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_name": {"type": "string"},
                },
                "required": ["task_id", "agent_name"],
            },
            handler=handler,
        )

    def _add_task_tool(self, actions: list[str]) -> PlannerTool:
        goal_repo, task_creation = self._goal_repo, self._task_creation

        def handler(inp: dict) -> str:
            goal_id = inp.get("goal_id", "")
            title = inp.get("title", "")
            description = inp.get("description", "")
            if not goal_id or not title:
                return json.dumps({"ok": False, "error": "goal_id and title are required."})

            try:
                goal = goal_repo.load(goal_id)
            except Exception:
                return json.dumps({"ok": False, "error": f"Goal {goal_id} not found."})

            tid = inp.get("task_id") or f"task-{uuid4().hex[:6]}"
            task_branch = f"goal/{goal.name}/task/{tid}"

            try:
                task = task_creation.create_task(
                    task_id=tid,
                    title=title,
                    description=description,
                    capability=inp.get("capability", "code:backend"),
                    files_allowed_to_modify=inp.get("files_allowed_to_modify", []),
                    feature_id=goal_id,
                    acceptance_criteria=inp.get("acceptance_criteria", []),
                    depends_on=inp.get("depends_on", []),
                    max_retries=2,
                    min_version=">=1.0.0",
                    constraints={"goal_branch": goal.branch, "task_branch": task_branch},
                )

                from src.domain.aggregates.goal import TaskSummary
                from src.domain.value_objects.status import TaskStatus

                goal.append_task_summary(
                    TaskSummary(
                        task_id=task.task_id,
                        title=task.title,
                        status=TaskStatus.CREATED,
                        branch=task_branch,
                        depends_on=task.depends_on,
                    )
                )
                goal_repo.save(goal)

                actions.append(f"New task `{tid}` added to goal `{goal_id}`: {title}")
                return json.dumps({"ok": True, "task_id": tid})
            except Exception as exc:
                return json.dumps({"ok": False, "error": str(exc)})

        return PlannerTool(
            name="add_task",
            description="Insert a new task into an active goal.",
            input_schema={
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "capability": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "files_allowed_to_modify": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["goal_id", "title", "description", "capability"],
            },
            handler=handler,
        )

    def _explain_task_tool(self) -> PlannerTool:
        task_repo, goal_repo = self._task_repo, self._goal_repo

        def handler(inp: dict) -> str:
            task_id = inp.get("task_id", "")
            try:
                task = task_repo.load(task_id)
            except Exception:
                return json.dumps({"error": f"Task {task_id} not found."})

            goal_info = None
            for goal in goal_repo.list_all():
                if task_id in goal.tasks:
                    goal_info = {
                        "goal_id": goal.goal_id,
                        "goal_name": goal.name,
                        "goal_status": goal.status.value,
                    }
                    break

            all_tasks = {t.task_id: t for g in goal_repo.list_all() for t in g.tasks.values()}
            blocking = [
                dep
                for dep in (task.depends_on or [])
                if dep in all_tasks and all_tasks[dep].status.value not in _SUCCESSFUL_DEPENDENCY_STATUSES
            ]

            return json.dumps(
                {
                    "task_id": task_id,
                    "title": task.title,
                    "status": task.status.value,
                    "capability": task.agent_selector.required_capability,
                    "goal": goal_info,
                    "depends_on": task.depends_on or [],
                    "blocking_dependencies": blocking,
                    "assigned_agent": task.assignment.agent_id if task.assignment else None,
                    "retry_count": task.retry_policy.attempt,
                    "max_retries": task.retry_policy.max_retries,
                    "description": task.description,
                    "acceptance_criteria": task.execution.acceptance_criteria or [],
                }
            )

        return PlannerTool(
            name="explain_task",
            description="Read-only. Return the full state of a task to answer 'why is X stuck?'",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
            handler=handler,
        )

    def _build_prompt(self, user_message: str, focused_task: Optional[dict]) -> str:
        goals_summary = ""
        try:
            for goal in self._goal_repo.list_all():
                mutable = [t for t in goal.tasks.values() if t.status.value in _MUTABLE_STATUSES]
                locked = [t for t in goal.tasks.values() if t.status.value not in _MUTABLE_STATUSES]
                goals_summary += (
                    f"\nGoal: {goal.name} ({goal.goal_id}) [{goal.status.value}]\n"
                    f"  Mutable tasks: {[t.task_id + ':' + t.title for t in mutable]}\n"
                    f"  Locked tasks:  {[t.task_id + ':' + t.status.value for t in locked]}\n"
                )
        except Exception as exc:
            log.warning("run_refinement.goals_summary_failed", error=str(exc))

        focus_section = f"## Focused Task\n{json.dumps(focused_task, indent=2)}\n" if focused_task else ""

        return f"""You are AIPOM — Agent-aware Interactive Planning for Orchestrated Multi-agent systems.
A human operator is refining a running execution plan. Do not ask clarifying questions.
Make the minimum changes needed. Stop after calling the relevant tools.

## Current Goals and Mutable Tasks
{goals_summary}
{focus_section}

## Operator message
{user_message}
"""

    @staticmethod
    def _coerce_list_value(value: object) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [item.strip() for item in value.split(",") if item.strip()]
            return parsed if isinstance(parsed, list) else [parsed]
        return [value]

    @staticmethod
    def _publish_task_updated(payload: dict, event_port: EventPort) -> None:
        try:
            from src.domain.events.domain_event import DomainEvent

            event_port.publish(
                DomainEvent(
                    type="task.updated",
                    producer="aipom-refinement",
                    payload=payload,
                )
            )
        except Exception as exc:
            log.warning("run_refinement.publish_failed", error=str(exc))
