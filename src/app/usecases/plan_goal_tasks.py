"""
src/app/usecases/plan_goal_tasks.py — Tactical JIT Planner (Tier 2).

Triggered just before a Goal is executed (via the ``goal.unblocked`` event).
Calls the LLM to generate exactly two TDD-adversarial tasks for the goal:

  Task 1 — Test Writer:   writes failing tests in ``tests/*``
  Task 2 — Implementer:   writes implementation to pass the tests in ``src/*``
                          (depends on Task 1)

This use case is intentionally narrow:
  - It never touches the Strategic Planner output.
  - It only runs when the GoalAggregate has 0 tasks.
  - It saves the updated GoalAggregate back to the repository after appending
    the two TaskSummary stubs.

Hexagonal Architecture notes:
  - ``domain`` has no dependencies — only ``GoalAggregate``, ``TaskSummary``,
    ``GoalTaskDef``, and ``TaskStatus`` are imported from the domain layer.
  - ``app`` coordinates: this use case orchestrates the LLM call and calls
    ``TaskCreationService``.
  - ``infra`` does I/O: ``PlannerRuntimePort`` is an abstract port; the
    concrete adapter is injected from the container.
"""
from __future__ import annotations

import json
from typing import Optional

import structlog

from src.domain import (
    DomainEvent,
    EventPort,
    GoalTaskDef,
    TaskStatus,
    TaskSummary,
)
from src.domain.aggregates.goal import GoalAggregate
from src.domain.ports.planner import (
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)
from src.domain.project_spec import ProjectSpecRepository
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.app.services.task_creation import TaskCreationService

log = structlog.get_logger(__name__)

PRODUCER = "jit-planner"
MAX_CAS_RETRIES = 5


class PlanGoalTasksUseCase:
    """
    Tactical JIT Planner: generate TDD task pairs for a single Goal.

    Call ``execute(goal_id)`` when a Goal becomes unblocked and has no tasks.
    The use case makes one LLM session (max 3 turns) and persists the two
    generated ``TaskAggregate`` objects plus their ``TaskSummary`` stubs on
    the ``GoalAggregate``.
    """

    def __init__(
        self,
        task_creation: TaskCreationService,
        goal_repo: GoalRepositoryPort,
        planner_runtime: PlannerRuntimePort,
        event_port: EventPort,
        spec_repo: Optional[ProjectSpecRepository] = None,
    ) -> None:
        self._task_creation = task_creation
        self._goal_repo = goal_repo
        self._planner_runtime = planner_runtime
        self._events = event_port
        self._spec_repo = spec_repo

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, goal_id: str) -> None:
        """
        Generate TDD tasks for *goal_id*.

        No-ops gracefully if:
          - The goal is not found.
          - The goal already has tasks (idempotency guard).
          - The LLM fails to produce valid tasks (logs error, does not raise).
        """
        goal = self._goal_repo.get(goal_id)
        if goal is None:
            log.warning("plan_goal_tasks.goal_not_found", goal_id=goal_id)
            return

        if goal.tasks:
            log.info(
                "plan_goal_tasks.already_has_tasks",
                goal_id=goal_id,
                task_count=len(goal.tasks),
            )
            return

        log.info(
            "plan_goal_tasks.starting",
            goal_id=goal_id,
            goal_name=goal.name,
            goal_description=goal.description,
        )

        task_defs = self._invoke_llm(goal)
        if not task_defs:
            log.error(
                "plan_goal_tasks.llm_returned_no_tasks",
                goal_id=goal_id,
            )
            return

        self._persist_tasks(goal, task_defs)

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _invoke_llm(self, goal: GoalAggregate) -> list[GoalTaskDef]:
        """
        Run a short planning session and return the extracted GoalTaskDefs.
        Returns an empty list on any failure.
        """
        extracted: list[GoalTaskDef] = []

        def submit_tdd_tasks_handler(inp: dict) -> str:
            """Tool handler — validate and store the two TDD task definitions."""
            nonlocal extracted
            raw = inp.get("tasks_json", "[]")
            try:
                tasks_data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(tasks_data, list):
                    raise ValueError("tasks_json must be a JSON array.")
                if len(tasks_data) != 2:
                    raise ValueError(
                        f"Exactly 2 tasks required (test-writer + implementer), "
                        f"got {len(tasks_data)}."
                    )
                parsed: list[GoalTaskDef] = []
                for td in tasks_data:
                    parsed.append(GoalTaskDef(**td))
                extracted = parsed
                return json.dumps({"accepted": True, "task_count": len(parsed)})
            except Exception as exc:
                return json.dumps({"accepted": False, "error": str(exc)})

        tdd_tool = PlannerTool(
            name="submit_tdd_tasks",
            description=(
                "Submit exactly two TDD task definitions for this goal. "
                "Task 1 must be the test-writer; Task 2 must be the implementer "
                "and MUST declare Task 1's task_id in its depends_on list."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tasks_json": {
                        "type": "string",
                        "description": (
                            "JSON array of exactly 2 task objects. Each object must have: "
                            "task_id (slug), title, description, capability, "
                            "files_allowed_to_modify (list), depends_on (list), "
                            "acceptance_criteria (list), test_command (str or null). "
                            "Example: "
                            '[{"task_id": "write-tests", "title": "Write failing tests", '
                            '"description": "...", "capability": "coding", '
                            '"files_allowed_to_modify": ["tests/*"], "depends_on": [], '
                            '"acceptance_criteria": ["Tests fail before impl"], '
                            '"test_command": null}, '
                            '{"task_id": "implement", "title": "Implement to pass tests", '
                            '"description": "...", "capability": "coding", '
                            '"files_allowed_to_modify": ["src/*"], '
                            '"depends_on": ["write-tests"], '
                            '"acceptance_criteria": ["All tests pass"], '
                            '"test_command": "pytest tests/"}]'
                        ),
                    }
                },
                "required": ["tasks_json"],
            },
            handler=submit_tdd_tasks_handler,
        )

        prompt = self._build_prompt(goal)

        try:
            self._planner_runtime.run_session(
                prompt=prompt,
                tools=[tdd_tool],
                max_turns=3,
            )
        except PlannerRuntimeError as exc:
            log.error(
                "plan_goal_tasks.runtime_error",
                goal_id=goal.goal_id,
                error=str(exc),
            )
            return []

        return extracted

    def _build_prompt(self, goal: GoalAggregate) -> str:
        """Construct the JIT planning prompt for the LLM."""
        arch_context = ""
        if self._spec_repo is not None:
            try:
                # Best-effort: include architecture constraints for context.
                spec = self._spec_repo.load(goal.name.split("-")[0] if goal.name else "")
                arch_context = (
                    f"\n\n## Architecture Constraints\n"
                    f"- Domain: {spec.objective.domain}\n"
                    f"- Tech stack: {spec.tech_stack}\n"
                )
            except Exception:
                pass  # spec unavailable — continue without it

        return (
            "You are the Tactical Goal Planner operating in TDD-adversarial mode.\n\n"
            f"## Goal to Implement\n"
            f"**Name:** {goal.name}\n"
            f"**Description:** {goal.description}\n"
            f"{arch_context}\n"
            "## Your Task\n\n"
            "You MUST call `submit_tdd_tasks` with EXACTLY TWO tasks:\n\n"
            "**Task 1 — Test Writer**\n"
            "- Writes failing tests that define the contract for this goal.\n"
            "- `files_allowed_to_modify` MUST be limited to `tests/*`.\n"
            "- `depends_on` must be `[]`.\n"
            "- `capability` should be `\"coding\"`.\n\n"
            "**Task 2 — Implementer**\n"
            "- Writes the implementation to make Task 1's tests pass.\n"
            "- `files_allowed_to_modify` MUST be limited to `src/*`.\n"
            "- `depends_on` MUST reference Task 1's `task_id`.\n"
            "- `capability` should be `\"coding\"`.\n\n"
            "Call `submit_tdd_tasks` now with a `tasks_json` array containing "
            "both task definitions."
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_tasks(
        self, goal: GoalAggregate, task_defs: list[GoalTaskDef]
    ) -> None:
        """
        Create TaskAggregates, append TaskSummary stubs to the GoalAggregate,
        and save everything.  Uses CAS retries for optimistic concurrency.
        """
        # Create task aggregates first (idempotent if task_id already exists).
        for tdef in task_defs:
            task_branch = f"goal/{goal.name}/task/{tdef.task_id}"
            constraints = {
                **tdef.constraints,
                "goal_branch": goal.branch,
                "task_branch": task_branch,
            }
            self._task_creation.create_task(
                task_id=tdef.task_id,
                title=tdef.title,
                description=tdef.description,
                capability=tdef.capability,
                files_allowed_to_modify=tdef.files_allowed_to_modify,
                feature_id=goal.goal_id,
                test_command=tdef.test_command,
                acceptance_criteria=tdef.acceptance_criteria,
                depends_on=tdef.depends_on,
                max_retries=tdef.max_retries,
                min_version=tdef.min_version,
                constraints=constraints,
            )
            log.info(
                "plan_goal_tasks.task_created",
                goal_id=goal.goal_id,
                task_id=tdef.task_id,
            )

        # Append TaskSummary stubs to GoalAggregate with CAS retry.
        for attempt in range(MAX_CAS_RETRIES):
            fresh_goal = self._goal_repo.get(goal.goal_id)
            if fresh_goal is None:
                log.error("plan_goal_tasks.goal_disappeared", goal_id=goal.goal_id)
                return

            expected_v = fresh_goal.state_version

            for tdef in task_defs:
                task_branch = f"goal/{goal.name}/task/{tdef.task_id}"
                summary = TaskSummary(
                    task_id=tdef.task_id,
                    title=tdef.title,
                    status=TaskStatus.CREATED,
                    branch=task_branch,
                    depends_on=tdef.depends_on,
                )
                fresh_goal.append_task_summary(summary)

            if self._goal_repo.update_if_version(goal.goal_id, fresh_goal, expected_v):
                log.info(
                    "plan_goal_tasks.goal_updated",
                    goal_id=goal.goal_id,
                    task_ids=[t.task_id for t in task_defs],
                )
                return

            log.warning(
                "plan_goal_tasks.cas_conflict",
                goal_id=goal.goal_id,
                attempt=attempt,
            )

        log.error("plan_goal_tasks.cas_exhausted", goal_id=goal.goal_id)
