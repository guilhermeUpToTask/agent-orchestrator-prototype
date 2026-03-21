"""
tests/unit/domain/goal/test_goal_review.py

Tests derived directly from the code review findings. Each class maps to one
reviewed concern.  The pre-existing test suite is not duplicated here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock, call

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.aggregates.task import TaskAggregate
from src.domain.value_objects.goal import GoalSpec, GoalTaskDef, _has_cycle
from src.domain.value_objects.status import TaskStatus
from src.domain.value_objects.task import AgentSelector, Assignment, ExecutionSpec, TaskResult
from src.domain.events.domain_event import DomainEvent
from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter


# ===========================================================================
# Shared doubles (duplicated minimally — keep tests self-contained)
# ===========================================================================

class _GoalRepo:
    def __init__(self):
        self._s: dict = {}

    def save(self, g):
        self._s[g.goal_id] = g.model_copy(deep=True)

    def load(self, gid):
        if gid not in self._s:
            raise KeyError(gid)
        return self._s[gid].model_copy(deep=True)

    def update_if_version(self, gid, new, v):
        cur = self._s.get(gid)
        if cur is None or cur.state_version != v:
            return False
        self._s[gid] = new.model_copy(deep=True)
        return True

    def list_all(self):
        return [g.model_copy(deep=True) for g in self._s.values()]

    def get(self, gid):
        v = self._s.get(gid)
        return v.model_copy(deep=True) if v else None


class _TaskRepo:
    def __init__(self):
        self._s: dict = {}

    def save(self, t):
        self._s[t.task_id] = t.model_copy(deep=True)

    def load(self, tid):
        if tid not in self._s:
            raise KeyError(tid)
        return self._s[tid].model_copy(deep=True)

    def update_if_version(self, tid, new, v):
        cur = self._s.get(tid)
        if cur is None or cur.state_version != v:
            return False
        self._s[tid] = new.model_copy(deep=True)
        return True

    def list_all(self):
        return [t.model_copy(deep=True) for t in self._s.values()]

    def get(self, tid):
        v = self._s.get(tid)
        return v.model_copy(deep=True) if v else None

    def append_history(self, *a, **kw): pass
    def delete(self, tid): return True


class _Git:
    def __init__(self):
        self.branches = []
        self.merges = []

    def create_goal_branch(self, url, branch):
        self.branches.append(branch)

    def merge_task_into_goal(self, repo_url, task_branch, goal_branch, commit_message=""):
        self.merges.append(dict(task=task_branch, goal=goal_branch))
        return "deadbeef0000"

    def create_workspace(self, *a): return "/tmp/ws"
    def checkout_main_and_create_branch(self, *a, **kw): pass
    def apply_changes_and_commit(self, *a): return "sha"
    def push_branch(self, *a, **kw): pass
    def cleanup_workspace(self, *a): pass
    def get_modified_files(self, *a): return []


def _make_task(task_id: str, goal_id: str,
               status: TaskStatus = TaskStatus.SUCCEEDED) -> TaskAggregate:
    t = TaskAggregate.create(
        task_id=task_id,
        title=f"T{task_id}",
        description="d",
        agent_selector=AgentSelector(required_capability="coding"),
        execution=ExecutionSpec(
            type="coding",
            constraints={
                "goal_branch": "goal/test",
                "task_branch": f"goal/test/task/{task_id}",
            },
        ),
        feature_id=goal_id,
    )
    if status in (TaskStatus.SUCCEEDED, TaskStatus.MERGED, TaskStatus.FAILED):
        t.assign(Assignment(agent_id="a1"))
        t.start()
        if status in (TaskStatus.SUCCEEDED, TaskStatus.MERGED):
            t.complete(TaskResult(branch=f"goal/test/task/{task_id}", commit_sha="s"))
        elif status == TaskStatus.FAILED:
            t.fail("agent crashed")
    if status == TaskStatus.MERGED:
        t.mark_merged()
    return t


def _running_goal(goal_id="g001", task_ids=("t1",)) -> GoalAggregate:
    g = GoalAggregate.create(
        name="test",
        description="d",
        goal_id=goal_id,
        task_summaries=[
            TaskSummary(task_id=tid, title=tid, status=TaskStatus.CREATED,
                        branch=f"goal/test/task/{tid}")
            for tid in task_ids
        ],
    )
    g.start()
    return g


# ===========================================================================
# BUG 1: GoalSpec rejects empty task list
# ===========================================================================

class TestGoalSpecEmptyTasks:

    def test_empty_tasks_raises(self):
        with pytest.raises(ValidationError, match="at least one task"):
            GoalSpec(name="my-goal", description="d", tasks=[])

    def test_single_task_valid(self):
        spec = GoalSpec(
            name="my-goal", description="d",
            tasks=[GoalTaskDef(task_id="t1", title="T", description="d",
                               capability="coding")],
        )
        assert len(spec.tasks) == 1


# ===========================================================================
# BUG 2: GoalSpec.name and GoalTaskDef.task_id must be branch-safe slugs
# ===========================================================================

class TestSlugValidation:

    @pytest.mark.parametrize("name", [
        "my goal",       # space
        "My-Goal",       # uppercase
        "-bad",          # leading hyphen
        "bad/slash",     # slash
        "bad.dot",       # dot
        "",              # empty
    ])
    def test_invalid_goal_name_rejected(self, name):
        with pytest.raises(ValidationError, match="not a valid name"):
            GoalSpec(
                name=name, description="d",
                tasks=[GoalTaskDef(task_id="t1", title="T", description="d",
                                   capability="coding")],
            )

    @pytest.mark.parametrize("name", ["my-goal", "auth-layer", "v2", "a1b2c3"])
    def test_valid_goal_names_accepted(self, name):
        spec = GoalSpec(
            name=name, description="d",
            tasks=[GoalTaskDef(task_id="t1", title="T", description="d",
                               capability="coding")],
        )
        assert spec.name == name

    @pytest.mark.parametrize("tid", [
        "my task",       # space
        "MyTask",        # uppercase
        "-bad",          # leading hyphen
        "bad/slash",     # slash
    ])
    def test_invalid_task_id_rejected(self, tid):
        with pytest.raises(ValidationError, match="not a valid task_id"):
            GoalTaskDef(task_id=tid, title="T", description="d", capability="coding")

    @pytest.mark.parametrize("tid", ["setup-deps", "add-auth", "step-1", "t1"])
    def test_valid_task_ids_accepted(self, tid):
        tdef = GoalTaskDef(task_id=tid, title="T", description="d", capability="coding")
        assert tdef.task_id == tid


# ===========================================================================
# BUG 3: GoalInitUseCase blocks on name collision even without explicit goal_id
# ===========================================================================

class TestGoalInitIdempotency:

    def _build(self):
        from src.app.usecases.goal_init import GoalInitUseCase
        from src.app.services.task_creation import TaskCreationService

        goal_repo = _GoalRepo()
        task_repo = _TaskRepo()
        events    = InMemoryEventAdapter()
        git       = _Git()
        svc       = TaskCreationService(task_repo=task_repo, event_port=events)
        uc = GoalInitUseCase(
            goal_repo=goal_repo, task_repo=task_repo, event_port=events,
            git_workspace=git, task_creation=svc, repo_url="file:///r",
        )
        return uc, goal_repo

    def _spec(self, name="auth-layer", goal_id=None):
        return GoalSpec(
            goal_id=goal_id,
            name=name,
            description="d",
            tasks=[GoalTaskDef(task_id="t1", title="T", description="d",
                               capability="coding")],
        )

    def test_explicit_goal_id_blocked_on_repeat(self):
        uc, _ = self._build()
        spec = self._spec(goal_id="goal-fixed")
        uc.execute(spec)
        with pytest.raises(ValueError, match="already exists"):
            uc.execute(spec)

    def test_no_goal_id_blocked_on_name_collision(self):
        """Bug 3: second call with same name but no goal_id must be rejected."""
        uc, _ = self._build()
        spec = self._spec()           # goal_id=None
        uc.execute(spec)
        with pytest.raises(ValueError, match="already exists"):
            uc.execute(self._spec())  # same name, different auto-id

    def test_different_name_allowed(self):
        uc, _ = self._build()
        uc.execute(self._spec(name="feature-a"))
        uc.execute(self._spec(name="feature-b"))  # must not raise


# ===========================================================================
# BUG 4: dead imports removed — verify goal_init imports are clean
# ===========================================================================

class TestGoalInitImports:

    def test_no_unused_domain_imports(self):
        """AgentSelector and ExecutionSpec must not be imported in goal_init."""
        import ast, pathlib
        src = pathlib.Path(
            "src/app/usecases/goal_init.py"
        ).read_text()
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "AgentSelector" not in imported_names, \
            "AgentSelector is imported but unused — remove it"
        assert "ExecutionSpec" not in imported_names, \
            "ExecutionSpec is imported but unused — remove it"


# ===========================================================================
# BUG 5: orchestrator._on_task_assigned retries on CAS conflict
# ===========================================================================

class TestOrchestratorCASRetry:

    def _build(self, fail_first_n=0):
        from src.app.orchestrator import TaskGraphOrchestrator
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase
        from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase

        task_repo = _TaskRepo()
        goal_repo = _GoalRepo()
        events    = InMemoryEventAdapter()

        task = _make_task("t1", goal_id="g-retry", status=TaskStatus.SUCCEEDED)
        task_repo.save(task)

        goal = _running_goal("g-retry", task_ids=("t1",))
        # Reset to PENDING so the orchestrator will try to start it
        goal.status = GoalStatus.PENDING
        goal_repo.save(goal)

        # Wrap update_if_version to simulate n initial CAS failures
        _calls = [0]
        _real = goal_repo.update_if_version

        def _flaky(gid, new, v):
            if _calls[0] < fail_first_n:
                _calls[0] += 1
                return False          # simulate conflict
            return _real(gid, new, v)

        goal_repo.update_if_version = _flaky

        merge_uc  = GoalMergeTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo,
            event_port=events, git_workspace=_Git(), repo_url="file:///",
        )
        cancel_uc = GoalCancelTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo, event_port=events,
        )
        orch = TaskGraphOrchestrator(
            task_repo=task_repo, goal_repo=goal_repo, event_port=events,
            merge_usecase=merge_uc, cancel_usecase=cancel_uc,
        )
        return orch, goal_repo

    def test_goal_started_after_two_cas_conflicts(self):
        """Retry loop must succeed even when the first two CAS writes fail."""
        orch, goal_repo = self._build(fail_first_n=2)
        ev = DomainEvent(type="task.assigned", producer="test",
                         payload={"task_id": "t1"})
        orch._dispatch(ev)
        assert goal_repo.load("g-retry").status == GoalStatus.RUNNING

    def test_goal_started_on_first_try(self):
        orch, goal_repo = self._build(fail_first_n=0)
        ev = DomainEvent(type="task.assigned", producer="test",
                         payload={"task_id": "t1"})
        orch._dispatch(ev)
        assert goal_repo.load("g-retry").status == GoalStatus.RUNNING

    def test_already_running_goal_is_not_restarted(self):
        orch, goal_repo = self._build()
        # Manually set goal to RUNNING before the event
        g = goal_repo.load("g-retry")
        g.start()
        goal_repo.save(g)
        v_before = goal_repo.load("g-retry").state_version

        ev = DomainEvent(type="task.assigned", producer="test",
                         payload={"task_id": "t1"})
        orch._dispatch(ev)
        # start() is idempotent on a RUNNING goal, so version must not bump again
        assert goal_repo.load("g-retry").state_version == v_before


# ===========================================================================
# BUG 6: GoalMergeTaskUseCase skips mark_merged when task is not SUCCEEDED
# ===========================================================================

class TestMergeTaskNotSucceeded:

    def _build_merge_uc(self, task_status: TaskStatus):
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase

        task_repo = _TaskRepo()
        goal_repo = _GoalRepo()
        events    = InMemoryEventAdapter()
        git       = _Git()

        task = _make_task("t1", goal_id="g001", status=task_status)
        task_repo.save(task)

        goal = _running_goal("g001", task_ids=("t1",))
        goal_repo.save(goal)

        uc = GoalMergeTaskUseCase(
            task_repo=task_repo, goal_repo=goal_repo,
            event_port=events, git_workspace=git, repo_url="file:///",
        )
        return uc, task_repo, goal_repo, git

    def test_failed_task_skips_mark_merged(self):
        """
        Bug 6: If task arrives as FAILED (race), mark_merged must not be
        called — instead the use case should log and return cleanly.
        """
        uc, task_repo, goal_repo, git = self._build_merge_uc(TaskStatus.FAILED)
        uc.execute("t1")
        # Git merge happened (git side is separate from task state guard)
        # but task must NOT be MERGED
        assert task_repo.load("t1").status == TaskStatus.FAILED

    def test_already_merged_task_is_idempotent(self):
        """Calling execute on a task that is already MERGED must not re-merge."""
        uc, _, _, git = self._build_merge_uc(TaskStatus.MERGED)
        uc.execute("t1")
        # Git merge should still happen (git side is idempotent no-ff merge)
        # but the task CAS loop returns early without error
        assert len(git.merges) == 1  # git merge still called — that's fine

    def test_succeeded_task_is_marked_merged(self):
        uc, task_repo, _, _ = self._build_merge_uc(TaskStatus.SUCCEEDED)
        uc.execute("t1")
        assert task_repo.load("t1").status == TaskStatus.MERGED


# ===========================================================================
# BUG 7: GoalFinalizeUseCase rejects double-finalize
# ===========================================================================

class TestFinalizeDoubleCall:

    def _build(self):
        from src.app.usecases.goal_finalize import GoalFinalizeUseCase

        goal_repo = _GoalRepo()
        events    = InMemoryEventAdapter()
        git       = _Git()

        goal = GoalAggregate.create(
            name="feat", description="d", goal_id="g-fin",
            task_summaries=[
                TaskSummary(task_id="t1", title="T1", status=TaskStatus.MERGED,
                            branch="goal/feat/task/t1"),
            ],
        )
        goal.start()
        goal.record_task_merged("t1")   # → READY_FOR_REVIEW
        goal.open_pr(1, "http://url", "sha")
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha", approval_count=1)
        goal.advance_from_pr_state()    # → APPROVED
        goal_repo.save(goal)

        uc = GoalFinalizeUseCase(
            goal_repo=goal_repo, event_port=events,
        )
        return uc, goal_repo, events, git

    def test_first_finalize_succeeds(self):
        uc, *_ = self._build()
        result = uc.execute("g-fin")
        assert isinstance(result, dict)
        assert result["goal_id"] == "g-fin"

    def test_second_finalize_raises(self):
        uc, *_ = self._build()
        uc.execute("g-fin")
        with pytest.raises(ValueError, match="already been finalized"):
            uc.execute("g-fin")

    def test_finalize_emits_event_once(self):
        uc, _, events, _ = self._build()
        uc.execute("g-fin")
        assert len(events.events_of_type("goal.finalized")) == 1

    def test_finalize_records_history(self):
        uc, goal_repo, *_ = self._build()
        uc.execute("g-fin")
        history_events = [h.event for h in goal_repo.load("g-fin").history]
        assert "goal.finalized" in history_events

    def test_finalize_running_goal_raises(self):
        from src.app.usecases.goal_finalize import GoalFinalizeUseCase

        goal_repo = _GoalRepo()
        events    = InMemoryEventAdapter()

        goal = _running_goal("g-run", task_ids=("t1",))
        goal_repo.save(goal)

        uc = GoalFinalizeUseCase(
            goal_repo=goal_repo, event_port=events,
        )
        with pytest.raises(ValueError):
            uc.execute("g-run")

    def test_finalize_failed_goal_raises(self):
        from src.app.usecases.goal_finalize import GoalFinalizeUseCase

        goal_repo = _GoalRepo()
        goal = _running_goal("g-fail", task_ids=("t1",))
        goal.record_task_canceled("t1", "reason")
        goal_repo.save(goal)

        uc = GoalFinalizeUseCase(
            goal_repo=goal_repo, event_port=InMemoryEventAdapter(),
        )
        with pytest.raises(ValueError):
            uc.execute("g-fail")


# ===========================================================================
# Coverage gap: _has_cycle edge cases
# ===========================================================================

class TestHasCycle:

    def test_empty_graph(self):
        assert not _has_cycle({})

    def test_single_node_no_dep(self):
        assert not _has_cycle({"a": set()})

    def test_single_node_self_loop(self):
        assert _has_cycle({"a": {"a"}})

    def test_two_node_no_cycle(self):
        assert not _has_cycle({"a": set(), "b": {"a"}})

    def test_two_node_cycle(self):
        assert _has_cycle({"a": {"b"}, "b": {"a"}})

    def test_three_node_indirect_cycle(self):
        assert _has_cycle({"a": {"c"}, "b": {"a"}, "c": {"b"}})

    def test_diamond_no_cycle(self):
        # a→b, a→c, b→d, c→d
        assert not _has_cycle({
            "a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"}
        })

    def test_disconnected_components_with_cycle(self):
        # clean chain + separate cycle
        assert _has_cycle({
            "x": set(), "y": {"x"},         # clean
            "p": {"q"}, "q": {"p"},          # cycle
        })

    def test_disconnected_all_clean(self):
        assert not _has_cycle({
            "x": set(), "y": {"x"},
            "a": set(), "b": {"a"},
        })


# ===========================================================================
# Coverage gap: GoalInitUseCase._topological_order correctness
# ===========================================================================

class TestTopologicalOrder:
    """Tests for the private helper — invoked indirectly via execute()."""

    def _spec_with_tasks(self, *defs) -> GoalSpec:
        """Each def is (task_id, depends_on_list)."""
        tasks = [
            GoalTaskDef(task_id=tid, title=tid, description="d",
                        capability="coding", depends_on=deps)
            for tid, deps in defs
        ]
        return GoalSpec(name="ord-test", description="d", tasks=tasks)

    def _order(self, spec) -> list[str]:
        from src.app.usecases.goal_init import _topological_order
        return [t.task_id for t in _topological_order(spec)]

    def test_single_task(self):
        spec = self._spec_with_tasks(("a", []))
        assert self._order(spec) == ["a"]

    def test_linear_chain_a_b_c(self):
        spec = self._spec_with_tasks(("a", []), ("b", ["a"]), ("c", ["b"]))
        order = self._order(spec)
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond(self):
        spec = self._spec_with_tasks(
            ("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])
        )
        order = self._order(spec)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_independent_tasks_all_present(self):
        spec = self._spec_with_tasks(("x", []), ("y", []), ("z", []))
        order = self._order(spec)
        assert set(order) == {"x", "y", "z"}

    def test_output_contains_all_tasks(self):
        spec = self._spec_with_tasks(
            ("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b"]), ("e", ["c", "d"])
        )
        order = self._order(spec)
        assert len(order) == 5
        assert set(order) == {"a", "b", "c", "d", "e"}


# ===========================================================================
# Coverage gap: TaskCreationService constraint propagation
# ===========================================================================

class TestTaskCreationConstraints:

    def test_constraints_stored_on_execution_spec(self):
        from src.app.services.task_creation import TaskCreationService

        task_repo = _TaskRepo()
        events    = InMemoryEventAdapter()
        svc = TaskCreationService(task_repo=task_repo, event_port=events)

        task = svc.create_task(
            title="T", description="d", capability="coding",
            files_allowed_to_modify=[],
            constraints={"goal_branch": "goal/x", "task_branch": "goal/x/task/t1"},
        )
        assert task.execution.constraints["goal_branch"] == "goal/x"
        assert task.execution.constraints["task_branch"] == "goal/x/task/t1"

    def test_explicit_task_id_preserved(self):
        from src.app.services.task_creation import TaskCreationService

        task_repo = _TaskRepo()
        svc = TaskCreationService(task_repo=task_repo, event_port=InMemoryEventAdapter())
        task = svc.create_task(
            title="T", description="d", capability="coding",
            files_allowed_to_modify=[], task_id="my-custom-id",
        )
        assert task.task_id == "my-custom-id"
        assert task_repo.load("my-custom-id").task_id == "my-custom-id"

    def test_no_constraints_defaults_to_empty(self):
        from src.app.services.task_creation import TaskCreationService

        task_repo = _TaskRepo()
        svc = TaskCreationService(task_repo=task_repo, event_port=InMemoryEventAdapter())
        task = svc.create_task(
            title="T", description="d", capability="coding",
            files_allowed_to_modify=[],
        )
        assert task.execution.constraints == {}


# ===========================================================================
# Coverage gap: task_execute reads base_branch from constraints
# ===========================================================================

class TestPrepareWorkspaceBaseBranch:
    """Verify that _prepare_workspace passes goal_branch as base_branch to git."""

    def test_goal_branch_used_as_base(self):
        from src.app.usecases.task_execute import TaskExecuteUseCase

        # Build a minimal task with goal constraints
        task_repo = _TaskRepo()
        task = _make_task("t1", goal_id="g1", status=TaskStatus.SUCCEEDED)
        # Override to CREATED so task_execute can load and inspect constraints
        task2 = TaskAggregate.create(
            task_id="t1",
            title="T",
            description="d",
            agent_selector=AgentSelector(required_capability="coding"),
            execution=ExecutionSpec(
                type="coding",
                constraints={
                    "goal_branch": "goal/my-feat",
                    "task_branch": "goal/my-feat/task/t1",
                },
            ),
            feature_id="g1",
        )
        task_repo.save(task2)

        git_calls = []

        class _CapturingGit:
            def create_workspace(self, repo_url, task_id):
                return "/ws"

            def checkout_main_and_create_branch(self, ws, branch, base_branch="main"):
                git_calls.append(dict(branch=branch, base=base_branch))

            def cleanup_workspace(self, ws): pass
            def get_modified_files(self, ws): return []
            def push_branch(self, *a, **kw): pass
            def apply_changes_and_commit(self, ws, msg): return "sha"
            def create_goal_branch(self, *a): pass
            def merge_task_into_goal(self, *a, **kw): return "sha"

        from unittest.mock import MagicMock
        uc = TaskExecuteUseCase(
            repo_url="file:///",
            task_repo=task_repo,
            agent_registry=MagicMock(),
            event_port=InMemoryEventAdapter(),
            lease_port=MagicMock(),
            git_workspace=_CapturingGit(),
            runtime_factory=MagicMock(),
            logs_port=MagicMock(),
            test_runner=MagicMock(),
        )

        uc._prepare_workspace("t1")

        assert len(git_calls) == 1
        assert git_calls[0]["branch"] == "goal/my-feat/task/t1"
        assert git_calls[0]["base"] == "goal/my-feat"

    def test_standalone_task_uses_main_as_base(self):
        from src.app.usecases.task_execute import TaskExecuteUseCase

        task_repo = _TaskRepo()
        task = TaskAggregate.create(
            task_id="standalone",
            title="T",
            description="d",
            agent_selector=AgentSelector(required_capability="coding"),
            execution=ExecutionSpec(type="coding"),  # no constraints
        )
        task_repo.save(task)

        git_calls = []

        class _CaptureGit:
            def create_workspace(self, *a): return "/ws"
            def checkout_main_and_create_branch(self, ws, branch, base_branch="main"):
                git_calls.append(dict(branch=branch, base=base_branch))
            def cleanup_workspace(self, ws): pass
            def get_modified_files(self, ws): return []
            def push_branch(self, *a, **kw): pass
            def apply_changes_and_commit(self, ws, msg): return "sha"
            def create_goal_branch(self, *a): pass
            def merge_task_into_goal(self, *a, **kw): return "sha"

        from unittest.mock import MagicMock
        uc = TaskExecuteUseCase(
            repo_url="file:///",
            task_repo=task_repo,
            agent_registry=MagicMock(),
            event_port=InMemoryEventAdapter(),
            lease_port=MagicMock(),
            git_workspace=_CaptureGit(),
            runtime_factory=MagicMock(),
            logs_port=MagicMock(),
            test_runner=MagicMock(),
        )

        uc._prepare_workspace("standalone")

        assert git_calls[0]["branch"] == "task/standalone"
        assert git_calls[0]["base"] == "main"


# ===========================================================================
# Coverage gap: GoalAggregate._all_tasks_merged with empty tasks dict
# ===========================================================================

class TestGoalAggregateEdgeCases:

    def test_empty_goal_never_auto_completes(self):
        """
        _all_tasks_merged() must return False for a goal with no tasks,
        so create() is guarded by GoalSpec validation.
        """
        g = GoalAggregate(
            goal_id="g-empty",
            name="empty",
            description="d",
            branch="goal/empty",
            tasks={},
        )
        assert not g._all_tasks_merged()

    def test_progress_on_empty_goal(self):
        g = GoalAggregate(
            goal_id="g-empty",
            name="empty",
            description="d",
            branch="goal/empty",
            tasks={},
        )
        merged, total = g.progress()
        assert merged == 0
        assert total == 0

    def test_pending_task_ids_excludes_merged_and_canceled(self):
        g = GoalAggregate.create(
            name="test", description="d", goal_id="g1",
            task_summaries=[
                TaskSummary(task_id="t1", title="T1", status=TaskStatus.CREATED,
                            branch="goal/test/task/t1"),
                TaskSummary(task_id="t2", title="T2", status=TaskStatus.MERGED,
                            branch="goal/test/task/t2"),
                TaskSummary(task_id="t3", title="T3", status=TaskStatus.CANCELED,
                            branch="goal/test/task/t3"),
            ],
        )
        pending = g.pending_task_ids()
        assert pending == ["t1"]
        assert "t2" not in pending
        assert "t3" not in pending

    def test_updated_at_bumps_on_every_transition(self):
        import time
        g = GoalAggregate.create(
            name="test", description="d", goal_id="g1",
            task_summaries=[
                TaskSummary(task_id="t1", title="T", status=TaskStatus.CREATED,
                            branch="goal/test/task/t1"),
            ],
        )
        t0 = g.updated_at
        time.sleep(0.01)
        g.start()
        assert g.updated_at > t0

    def test_history_order_reflects_transition_sequence(self):
        g = GoalAggregate.create(
            name="test", description="d", goal_id="g1",
            task_summaries=[
                TaskSummary(task_id="t1", title="T", status=TaskStatus.CREATED,
                            branch="goal/test/task/t1"),
            ],
        )
        g.start()
        g.record_task_merged("t1")
        events = [h.event for h in g.history]
        assert events == ["goal.started", "goal.task_merged", "goal.ready_for_review"]

    def test_record_task_status_does_not_auto_complete(self):
        """
        record_task_status(SUCCEEDED) must not trigger completion logic —
        only record_task_merged() does that.
        """
        g = GoalAggregate.create(
            name="test", description="d", goal_id="g1",
            task_summaries=[
                TaskSummary(task_id="t1", title="T", status=TaskStatus.CREATED,
                            branch="goal/test/task/t1"),
            ],
        )
        g.start()
        g.record_task_status("t1", TaskStatus.SUCCEEDED)
        assert g.status == GoalStatus.RUNNING   # not COMPLETED
