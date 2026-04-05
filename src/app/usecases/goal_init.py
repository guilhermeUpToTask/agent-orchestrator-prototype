"""
src/app/usecases/goal_init.py — Goal initialization use case.

Orchestrates everything needed to go from a GoalSpec (parsed goal file) to
a running goal:

  1. Validate the spec (DAG, no cycles — enforced by GoalSpec itself)
  2. Build TaskSummary stubs and create the GoalAggregate (PENDING)
  3. Persist the GoalAggregate
  4. Create the goal branch on the target repo (goal/<n>)
  5. Create all TaskAggregates via TaskCreationService, injecting goal branch
     constraints so the worker names branches correctly
  6. Publish goal.created

Each task created here carries two constraint keys that the worker reads:
  "goal_branch":  "goal/<n>"
  "task_branch":  "goal/<n>/task/<task_id>"

The dependency ordering passed to TaskCreationService is intentional: tasks
with no depends_on are created (and events emitted) first, so the task
manager can immediately begin assigning them while tasks that depend on
others wait for their dependencies to succeed.
"""
from __future__ import annotations

import structlog

from src.domain import (
    DomainEvent,
    EventPort,
    GitWorkspacePort,
    GoalAggregate,
    GoalSpec,
    TaskStatus,
    TaskSummary,
)
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories import TaskRepositoryPort
from src.app.services.task_creation import TaskCreationService

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"


class GoalInitUseCase:
    """
    Idempotent goal initialization.

    If a GoalAggregate with the same goal_id already exists in the repository,
    the use case raises ValueError rather than creating a duplicate. Re-running
    a goal should be a deliberate operator action (reset + init).
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        task_repo: TaskRepositoryPort,
        event_port: EventPort,
        git_workspace: GitWorkspacePort,
        task_creation: TaskCreationService,
        repo_url: str,
    ) -> None:
        self._goal_repo     = goal_repo
        self._task_repo     = task_repo
        self._events        = event_port
        self._git           = git_workspace
        self._task_creation = task_creation
        self._repo_url      = repo_url

    def execute(self, spec: GoalSpec) -> GoalAggregate:
        """
        Initialize the goal from spec. Returns the created GoalAggregate.
        Raises ValueError if a goal with the same goal_id already exists.
        """
        # ------------------------------------------------------------------
        # 1. Idempotency guard
        # ------------------------------------------------------------------
        goal_id = spec.goal_id
        if goal_id and self._goal_repo.get(goal_id):
            raise ValueError(
                f"Goal '{goal_id}' already exists. "
                "Use reset + init to restart it."
            )

        # Guard by name too: two goals with the same name produce the same
        # branch ref on the remote, which would collide at push time.
        existing = self._goal_repo.list_all()
        name_collision = next((g for g in existing if g.name == spec.name), None)
        if name_collision:
            raise ValueError(
                f"A goal named '{spec.name}' already exists "
                f"(goal_id: {name_collision.goal_id}). "
                "Use reset + init to restart it."
            )

        # Validate cross-goal depends_on references exist in the repository.
        # GoalSpec only validates its own internal task DAG; cross-goal refs
        # are validated by Roadmap when goals are planned together. When
        # GoalInitUseCase is called directly (e.g. ad-hoc goal creation), we
        # must still catch dangling refs here so they don't produce permanently
        # blocked goals with no error message.
        if spec.depends_on:
            existing_names = {g.name for g in existing}
            unknown_deps = [d for d in spec.depends_on if d not in existing_names]
            if unknown_deps:
                raise ValueError(
                    f"Goal '{spec.name}' depends on {unknown_deps!r} which do "
                    "not exist in the goal repository. Create prerequisite goals "
                    "first, or use the planning layer to dispatch a full Roadmap "
                    "which validates all cross-goal references before dispatch."
                )

        # ------------------------------------------------------------------
        # 2. Build TaskSummary stubs and create GoalAggregate
        # ------------------------------------------------------------------
        task_summaries = [
            TaskSummary(
                task_id=tdef.task_id,
                title=tdef.title,
                status=TaskStatus.CREATED,
                branch=f"goal/{spec.name}/task/{tdef.task_id}",
                depends_on=tdef.depends_on,
            )
            for tdef in spec.tasks
        ]

        goal = GoalAggregate.create(
            name=spec.name,
            description=spec.description,
            task_summaries=task_summaries,
            goal_id=goal_id,
            depends_on=spec.depends_on,
            feature_tag=spec.feature_tag,
        )

        # ------------------------------------------------------------------
        # 3. Persist goal (PENDING)
        # ------------------------------------------------------------------
        self._goal_repo.save(goal)
        log.info("goal_init.goal_created", goal_id=goal.goal_id, name=goal.name)

        # ------------------------------------------------------------------
        # 4. Create goal branch on the target repo
        # ------------------------------------------------------------------
        self._git.create_goal_branch(self._repo_url, goal.branch)
        log.info("goal_init.branch_created", branch=goal.branch)

        # ------------------------------------------------------------------
        # 5. Create all tasks (dependency-ordered: roots first)
        #    Skipped when spec.tasks is empty — JIT planner fills them in.
        # ------------------------------------------------------------------
        if spec.tasks:
            ordered = _topological_order(spec)
            for tdef in ordered:
                task_branch = f"goal/{spec.name}/task/{tdef.task_id}"
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
                    "goal_init.task_created",
                    goal_id=goal.goal_id,
                    task_id=tdef.task_id,
                    depends_on=tdef.depends_on,
                )
        else:
            log.info(
                "goal_init.tasks_deferred",
                goal_id=goal.goal_id,
                name=goal.name,
                hint="JIT planner will generate tasks when goal is unblocked.",
            )

        # ------------------------------------------------------------------
        # 6. Emit goal.created
        # ------------------------------------------------------------------
        self._events.publish(DomainEvent(
            type="goal.created",
            producer=PRODUCER,
            payload={
                "goal_id":    goal.goal_id,
                "name":       goal.name,
                "branch":     goal.branch,
                "task_count": len(spec.tasks),
            },
        ))

        return goal


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _topological_order(spec: GoalSpec) -> list:
    """
    Return GoalTaskDefs in topological order (dependencies before dependents).
    Kahn's algorithm — assumes spec.tasks is already a valid DAG (validated
    by GoalSpec on construction).
    """
    from collections import defaultdict, deque

    in_degree: dict[str, int] = {t.task_id: 0 for t in spec.tasks}
    dependents: dict[str, list[str]] = defaultdict(list)

    for tdef in spec.tasks:
        for dep in tdef.depends_on:
            in_degree[tdef.task_id] += 1
            dependents[dep].append(tdef.task_id)

    by_id = {t.task_id: t for t in spec.tasks}
    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    result = []

    while queue:
        tid = queue.popleft()
        result.append(by_id[tid])
        for dep_tid in dependents[tid]:
            in_degree[dep_tid] -= 1
            if in_degree[dep_tid] == 0:
                queue.append(dep_tid)

    return result
