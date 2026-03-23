"""
tests/unit/app/usecases/goal/test_goal_usecases.py

Tests for the four goal use cases and the orchestrator dispatcher.
All infrastructure is replaced with in-memory doubles — no Redis, no git,
no filesystem.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.aggregates.task import TaskAggregate
from src.domain.value_objects.goal import GoalSpec, GoalTaskDef
from src.domain.value_objects.status import TaskStatus
from src.domain.value_objects.task import AgentSelector, ExecutionSpec
from src.domain.events.domain_event import DomainEvent
from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter


# ===========================================================================
# In-memory doubles
# ===========================================================================

class InMemoryGoalRepo:
    def __init__(self):
        self._store: dict[str, GoalAggregate] = {}

    def save(self, goal: GoalAggregate) -> None:
        self._store[goal.goal_id] = goal.model_copy(deep=True)

    def load(self, goal_id: str) -> GoalAggregate:
        if goal_id not in self._store:
            raise KeyError(goal_id)
        return self._store[goal_id].model_copy(deep=True)

    def update_if_version(self, goal_id: str, new_state: GoalAggregate,
                          expected_version: int) -> bool:
        current = self._store.get(goal_id)
        if current is None or current.state_version != expected_version:
            return False
        self._store[goal_id] = new_state.model_copy(deep=True)
        return True

    def list_all(self) -> list[GoalAggregate]:
        return [g.model_copy(deep=True) for g in self._store.values()]

    def get(self, goal_id: str) -> GoalAggregate | None:
        return self._store.get(goal_id, None) and self._store[goal_id].model_copy(deep=True)


class InMemoryTaskRepo:
    def __init__(self):
        self._store: dict[str, TaskAggregate] = {}

    def save(self, task: TaskAggregate) -> None:
        self._store[task.task_id] = task.model_copy(deep=True)

    def load(self, task_id: str) -> TaskAggregate:
        if task_id not in self._store:
            raise KeyError(task_id)
        return self._store[task_id].model_copy(deep=True)

    def update_if_version(self, task_id: str, new_state: TaskAggregate,
                          expected_version: int) -> bool:
        current = self._store.get(task_id)
        if current is None or current.state_version != expected_version:
            return False
        self._store[task_id] = new_state.model_copy(deep=True)
        return True

    def list_all(self) -> list[TaskAggregate]:
        return [t.model_copy(deep=True) for t in self._store.values()]

    def get(self, task_id: str) -> TaskAggregate | None:
        return self._store.get(task_id, None) and self._store[task_id].model_copy(deep=True)

    def append_history(self, task_id, event, actor, detail): pass
    def delete(self, task_id): return True


class StubGitWorkspace:
    """Records calls without doing any real git operations."""
    def __init__(self):
        self.goal_branches_created: list[str] = []
        self.merges: list[dict] = []

    def create_goal_branch(self, repo_url: str, goal_branch: str) -> None:
        self.goal_branches_created.append(goal_branch)

    def merge_task_into_goal(self, repo_url, task_branch, goal_branch,
                             commit_message="") -> str:
        self.merges.append(dict(
            task_branch=task_branch,
            goal_branch=goal_branch,
            commit_message=commit_message,
        ))
        return "abc123def456"

    # Unused by goal use cases but required by the port
    def create_workspace(self, repo_url, task_id): return "/tmp/ws"
    def checkout_main_and_create_branch(self, ws, branch, base_branch="main"): pass
    def apply_changes_and_commit(self, ws, msg): return "sha"
    def push_branch(self, ws, branch, remote="origin"): pass
    def cleanup_workspace(self, ws): pass
    def get_modified_files(self, ws): return []


# ===========================================================================
# Fixtures
# ===========================================================================

def _simple_spec(name: str = "test-goal") -> GoalSpec:
    return GoalSpec(
        name=name,
        description="test",
        tasks=[
            GoalTaskDef(
                task_id="task-a",
                title="Task A",
                description="do A",
                capability="coding",
                files_allowed_to_modify=["a.py"],
            ),
            GoalTaskDef(
                task_id="task-b",
                title="Task B",
                description="do B",
                capability="coding",
                files_allowed_to_modify=["b.py"],
                depends_on=["task-a"],
            ),
        ],
    )


def _make_task(task_id: str, goal_id: str,
               goal_branch: str = "goal/test-goal",
               status: TaskStatus = TaskStatus.SUCCEEDED) -> TaskAggregate:
    task = TaskAggregate.create(
        task_id=task_id,
        title=f"Task {task_id}",
        description="desc",
        agent_selector=AgentSelector(required_capability="coding"),
        execution=ExecutionSpec(
            type="coding",
            constraints={
                "goal_branch": goal_branch,
                "task_branch": f"{goal_branch}/task/{task_id}",
            },
        ),
        feature_id=goal_id,
    )
    # Fast-forward to the requested status
    if status in (TaskStatus.SUCCEEDED, TaskStatus.MERGED):
        from src.domain.value_objects.task import Assignment, TaskResult
        task.assign(Assignment(agent_id="agent-1"))
        task.start()
        task.complete(TaskResult(branch=f"{goal_branch}/task/{task_id}", commit_sha="sha"))
    if status == TaskStatus.MERGED:
        task.mark_merged()
    return task


# ===========================================================================
# GoalInitUseCase
# ===========================================================================

class TestGoalInitUseCase:
    def _build(self):
        from src.app.usecases.goal_init import GoalInitUseCase
        from src.app.services.task_creation import TaskCreationService

        goal_repo  = InMemoryGoalRepo()
        task_repo  = InMemoryTaskRepo()
        events     = InMemoryEventAdapter()
        git        = StubGitWorkspace()
        task_svc   = TaskCreationService(task_repo=task_repo, event_port=events)
        usecase    = GoalInitUseCase(
            goal_repo=goal_repo,
            task_repo=task_repo,
            event_port=events,
            git_workspace=git,
            task_creation=task_svc,
            repo_url="file:///repo",
        )
        return usecase, goal_repo, task_repo, events, git

    def test_creates_goal_aggregate(self):
        uc, goal_repo, *_ = self._build()
        spec = _simple_spec()
        goal = uc.execute(spec)
        assert goal.goal_id.startswith("goal-")
        assert goal_repo.get(goal.goal_id) is not None

    def test_goal_starts_pending(self):
        uc, *_ = self._build()
        goal = uc.execute(_simple_spec())
        assert goal.status == GoalStatus.PENDING

    def test_tasks_created_in_task_repo(self):
        uc, _, task_repo, *_ = self._build()
        uc.execute(_simple_spec())
        assert task_repo.get("task-a") is not None
        assert task_repo.get("task-b") is not None

    def test_tasks_carry_goal_constraints(self):
        uc, _, task_repo, *_ = self._build()
        goal = uc.execute(_simple_spec())
        task = task_repo.load("task-a")
        assert task.execution.constraints["goal_branch"] == f"goal/{goal.name}"
        assert "task-a" in task.execution.constraints["task_branch"]

    def test_task_feature_id_is_goal_id(self):
        uc, _, task_repo, *_ = self._build()
        goal = uc.execute(_simple_spec())
        assert task_repo.load("task-a").feature_id == goal.goal_id

    def test_goal_branch_created(self):
        uc, _, _, _, git = self._build()
        goal = uc.execute(_simple_spec())
        assert goal.branch in git.goal_branches_created

    def test_goal_created_event_emitted(self):
        uc, _, _, events, _ = self._build()
        uc.execute(_simple_spec())
        assert any(e.type == "goal.created" for e in events.all_events)

    def test_task_created_events_emitted(self):
        uc, _, _, events, _ = self._build()
        uc.execute(_simple_spec())
        created = [e for e in events.all_events if e.type == "task.created"]
        assert len(created) == 2

    def test_duplicate_goal_id_raises(self):
        uc, *_ = self._build()
        spec = GoalSpec(
            goal_id="goal-fixed",
            name="test-goal",
            description="d",
            tasks=[GoalTaskDef(task_id="x", title="X", description="d",
                               capability="coding")],
        )
        uc.execute(spec)
        with pytest.raises(ValueError, match="already exists"):
            uc.execute(spec)

    def test_topological_order_respected(self):
        """task-b depends on task-a; task-a must be created (emits event) first."""
        uc, _, _, events, _ = self._build()
        uc.execute(_simple_spec())
        created_ids = [
            e.payload["task_id"]
            for e in events.all_events
            if e.type == "task.created"
        ]
        assert created_ids.index("task-a") < created_ids.index("task-b")


# ===========================================================================
# GoalMergeTaskUseCase
# ===========================================================================

class TestGoalMergeTaskUseCase:
    def _build(self, with_goal=True, task_status=TaskStatus.SUCCEEDED):
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase

        task_repo = InMemoryTaskRepo()
        goal_repo = InMemoryGoalRepo()
        events    = InMemoryEventAdapter()
        git       = StubGitWorkspace()

        goal = GoalAggregate.create(
            name="test-goal",
            description="d",
            goal_id="goal-001",
            task_summaries=[
                TaskSummary(
                    task_id="task-a",
                    title="A",
                    status=TaskStatus.CREATED,
                    branch="goal/test-goal/task/task-a",
                ),
                TaskSummary(
                    task_id="task-b",
                    title="B",
                    status=TaskStatus.CREATED,
                    branch="goal/test-goal/task/task-b",
                ),
            ],
        )
        goal.start()
        if with_goal:
            goal_repo.save(goal)

        task = _make_task("task-a", goal_id="goal-001", status=task_status)
        task_repo.save(task)

        usecase = GoalMergeTaskUseCase(
            task_repo=task_repo,
            goal_repo=goal_repo,
            event_port=events,
            git_workspace=git,
            repo_url="file:///repo",
        )
        return usecase, task_repo, goal_repo, events, git

    def test_merges_task_branch(self):
        uc, _, _, _, git = self._build()
        uc.execute("task-a")
        assert len(git.merges) == 1
        assert git.merges[0]["task_branch"] == "goal/test-goal/task/task-a"
        assert git.merges[0]["goal_branch"] == "goal/test-goal"

    def test_task_marked_merged(self):
        uc, task_repo, *_ = self._build()
        uc.execute("task-a")
        assert task_repo.load("task-a").status == TaskStatus.MERGED

    def test_goal_task_summary_marked_merged(self):
        uc, _, goal_repo, *_ = self._build()
        uc.execute("task-a")
        goal = goal_repo.load("goal-001")
        assert goal.tasks["task-a"].status == TaskStatus.MERGED

    def test_goal_completed_when_all_merged(self):
        """Merging the only remaining task completes the goal."""
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase

        task_repo = InMemoryTaskRepo()
        goal_repo = InMemoryGoalRepo()
        events    = InMemoryEventAdapter()
        git       = StubGitWorkspace()

        # Single-task goal
        goal = GoalAggregate.create(
            name="solo", description="d", goal_id="goal-solo",
            task_summaries=[
                TaskSummary(task_id="t1", title="T1", status=TaskStatus.CREATED,
                            branch="goal/solo/task/t1"),
            ],
        )
        goal.start()
        goal_repo.save(goal)

        task = _make_task("t1", goal_id="goal-solo",
                          goal_branch="goal/solo", status=TaskStatus.SUCCEEDED)
        task_repo.save(task)

        uc = GoalMergeTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo,
            event_port=events, git_workspace=git, repo_url="file:///",
        )
        uc.execute("t1")

        assert goal_repo.load("goal-solo").status == GoalStatus.READY_FOR_REVIEW
        assert any(e.type == "goal.ready_for_review" for e in events.all_events)

    def test_no_op_for_task_without_goal(self):
        uc, _, _, _, git = self._build()
        task_no_goal = TaskAggregate.create(
            title="x", description="x",
            agent_selector=AgentSelector(required_capability="coding"),
            execution=ExecutionSpec(type="coding"),
        )
        # task has no feature_id
        # inject it manually
        uc._task_repo.save(task_no_goal)
        uc.execute(task_no_goal.task_id)
        assert git.merges == []

    def test_no_op_for_unknown_goal(self):
        uc, _, _, _, git = self._build(with_goal=False)
        uc.execute("task-a")
        assert git.merges == []

    def test_no_op_for_unknown_task(self):
        uc, _, _, _, git = self._build()
        uc.execute("ghost-task")
        assert git.merges == []

    def test_failed_task_does_not_merge_branch(self):
        uc, _, _, _, git = self._build(task_status=TaskStatus.FAILED)
        uc.execute("task-a")
        assert git.merges == []

    def test_already_merged_task_repairs_goal_without_remerging(self):
        uc, _, goal_repo, _, git = self._build(task_status=TaskStatus.MERGED)
        uc.execute("task-a")
        assert git.merges == []
        goal = goal_repo.load("goal-001")
        assert goal.tasks["task-a"].status == TaskStatus.MERGED


# ===========================================================================
# GoalCancelTaskUseCase
# ===========================================================================

class TestGoalCancelTaskUseCase:
    def _build(self):
        from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase

        task_repo = InMemoryTaskRepo()
        goal_repo = InMemoryGoalRepo()
        events    = InMemoryEventAdapter()

        goal = GoalAggregate.create(
            name="test-goal", description="d", goal_id="goal-002",
            task_summaries=[
                TaskSummary(task_id="task-x", title="X", status=TaskStatus.CREATED,
                            branch="goal/test-goal/task/task-x"),
            ],
        )
        goal.start()
        goal_repo.save(goal)

        task = _make_task("task-x", goal_id="goal-002", status=TaskStatus.SUCCEEDED)
        task_repo.save(task)

        uc = GoalCancelTaskUseCase(
            task_repo=task_repo,
            goal_repo=goal_repo,
            event_port=events,
        )
        return uc, goal_repo, events

    def test_fails_goal(self):
        uc, goal_repo, _ = self._build()
        uc.execute("task-x", "retries exhausted")
        assert goal_repo.load("goal-002").status == GoalStatus.FAILED

    def test_emits_goal_failed_event(self):
        uc, _, events = self._build()
        uc.execute("task-x", "retries exhausted")
        assert any(e.type == "goal.failed" for e in events.all_events)

    def test_no_op_for_already_failed_goal(self):
        uc, goal_repo, events = self._build()
        uc.execute("task-x", "first")
        count = len(events.all_events)
        uc.execute("task-x", "second")
        assert len(events.all_events) == count  # no new events

    def test_no_op_for_unknown_task(self):
        uc, _, events = self._build()
        uc.execute("ghost", "reason")
        assert not any(e.type == "goal.failed" for e in events.all_events)


# ===========================================================================
# GoalFinalizeUseCase
# ===========================================================================

class TestGoalFinalizeUseCase:
    def _build(self, status=GoalStatus.APPROVED):
        from src.app.usecases.goal_finalize import GoalFinalizeUseCase

        goal_repo = InMemoryGoalRepo()
        events    = InMemoryEventAdapter()
        git       = StubGitWorkspace()

        goal = GoalAggregate.create(
            name="ready-goal", description="d", goal_id="goal-fin",
            task_summaries=[
                TaskSummary(task_id="t1", title="T1", status=TaskStatus.MERGED,
                            branch="goal/ready-goal/task/t1"),
            ],
        )
        goal.start()
        if status == GoalStatus.APPROVED:
            goal.record_task_merged("t1")
            goal.open_pr(1, "http://url", "sha")
            goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                               head_sha="sha", approval_count=1)
            goal.advance_from_pr_state()
        elif status == GoalStatus.FAILED:
            goal.record_task_canceled("t1", "reason")
        goal_repo.save(goal)

        uc = GoalFinalizeUseCase(
            goal_repo=goal_repo,
            event_port=events,
        )
        return uc, goal_repo, events, git

    def test_records_finalization_in_history(self):
        uc, goal_repo, *_ = self._build()
        uc.execute("goal-fin")
        goal = goal_repo.load("goal-fin")
        events = [h.event for h in goal.history]
        assert "goal.finalized" in events

    def test_returns_summary_dict(self):
        uc, *_ = self._build()
        result = uc.execute("goal-fin")
        assert isinstance(result, dict)
        assert result["goal_id"] == "goal-fin"
        assert "pr_number" in result

    def test_emits_goal_finalized_event(self):
        uc, _, events, _ = self._build()
        uc.execute("goal-fin")
        assert any(e.type == "goal.finalized" for e in events.all_events)

    def test_raises_if_not_completed(self):
        uc, *_ = self._build(status=GoalStatus.FAILED)
        with pytest.raises(ValueError):
            uc.execute("goal-fin")

    def test_raises_if_goal_not_found(self):
        uc, *_ = self._build()
        with pytest.raises(KeyError):
            uc.execute("nonexistent-goal")


# ===========================================================================
# TaskGraphOrchestrator — event dispatch
# ===========================================================================

class TestTaskGraphOrchestrator:
    def _build(self):
        from src.app.orchestrator import TaskGraphOrchestrator
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase
        from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase

        task_repo = InMemoryTaskRepo()
        goal_repo = InMemoryGoalRepo()
        events    = InMemoryEventAdapter()
        git       = StubGitWorkspace()

        goal = GoalAggregate.create(
            name="orch-goal", description="d", goal_id="goal-orch",
            task_summaries=[
                TaskSummary(task_id="t1", title="T1", status=TaskStatus.CREATED,
                            branch="goal/orch-goal/task/t1"),
            ],
        )
        goal_repo.save(goal)

        task = _make_task("t1", goal_id="goal-orch",
                          goal_branch="goal/orch-goal", status=TaskStatus.SUCCEEDED)
        task_repo.save(task)

        merge_uc  = GoalMergeTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo,
            event_port=events, git_workspace=git, repo_url="file:///",
        )
        cancel_uc = GoalCancelTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo, event_port=events,
        )
        orch = TaskGraphOrchestrator(
            task_repo=task_repo, goal_repo=goal_repo,
            event_port=events,
            merge_usecase=merge_uc,
            cancel_usecase=cancel_uc,
        )
        return orch, goal_repo, task_repo, events, git

    def test_task_assigned_starts_goal(self):
        orch, goal_repo, task_repo, _, _ = self._build()
        event = DomainEvent(type="task.assigned", producer="test",
                            payload={"task_id": "t1"})
        orch._dispatch(event)
        assert goal_repo.load("goal-orch").status == GoalStatus.RUNNING

    def test_task_completed_triggers_merge(self):
        orch, _, _, _, git = self._build()
        # First start the goal
        start_ev = DomainEvent(type="task.assigned", producer="test",
                               payload={"task_id": "t1"})
        orch._dispatch(start_ev)
        # Then complete it
        complete_ev = DomainEvent(type="task.completed", producer="test",
                                  payload={"task_id": "t1"})
        orch._dispatch(complete_ev)
        assert len(git.merges) == 1

    def test_task_canceled_fails_goal(self):
        orch, goal_repo, *_ = self._build()
        event = DomainEvent(
            type="task.canceled", producer="test",
            payload={"task_id": "t1", "reason": "retries exhausted"},
        )
        orch._dispatch(event)
        assert goal_repo.load("goal-orch").status == GoalStatus.FAILED

    def test_dispatch_error_does_not_raise(self):
        """A broken use case must not crash the event loop."""
        orch, *_ = self._build()
        orch._merge = MagicMock(side_effect=RuntimeError("boom"))
        event = DomainEvent(type="task.completed", producer="test",
                            payload={"task_id": "t1"})
        orch._dispatch(event)  # must not raise

    def test_unknown_event_type_is_ignored(self):
        orch, _, _, _, git = self._build()
        event = DomainEvent(type="task.requeued", producer="test",
                            payload={"task_id": "t1"})
        orch._dispatch(event)
        assert git.merges == []
