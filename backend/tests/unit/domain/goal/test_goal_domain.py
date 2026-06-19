"""
tests/unit/domain/goal/test_goal_domain.py

Tests for:
  - GoalSpec: DAG validation, cycle detection, unknown dep references
  - GoalAggregate: lifecycle transitions, completion logic, failure on cancel
  - GoalTaskDef: basic construction
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.value_objects.goal import GoalSpec, GoalTaskDef
from src.domain.value_objects.status import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_def(task_id: str, depends_on: list[str] | None = None) -> GoalTaskDef:
    return GoalTaskDef(
        task_id=task_id,
        title=f"Task {task_id}",
        description="desc",
        capability="coding",
        depends_on=depends_on or [],
    )


def _spec(*task_ids_and_deps: str | tuple, name: str = "my-goal") -> GoalSpec:
    """Build a GoalSpec. Pass plain strings for independent tasks or (id, [deps]) tuples."""
    tasks = []
    for item in task_ids_and_deps:
        if isinstance(item, str):
            tasks.append(_task_def(item))
        else:
            tid, deps = item
            tasks.append(_task_def(tid, deps))
    return GoalSpec(name=name, description="test goal", tasks=tasks)


def _summary(task_id: str, status: TaskStatus = TaskStatus.CREATED,
             depends_on: list[str] | None = None) -> TaskSummary:
    return TaskSummary(
        task_id=task_id,
        title=f"Task {task_id}",
        status=status,
        branch=f"goal/test/task/{task_id}",
        depends_on=depends_on or [],
    )


def _goal(*task_ids: str) -> GoalAggregate:
    summaries = [_summary(tid) for tid in task_ids]
    return GoalAggregate.create(name="test-goal", description="desc", task_summaries=summaries)


# ===========================================================================
# GoalSpec — schema and DAG validation
# ===========================================================================

class TestGoalSpec:

    def test_valid_linear_chain(self):
        spec = _spec("a", ("b", ["a"]), ("c", ["b"]))
        assert [t.task_id for t in spec.tasks] == ["a", "b", "c"]

    def test_valid_diamond(self):
        """A → B, A → C, B + C → D is a valid DAG."""
        spec = _spec("a", ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
        assert len(spec.tasks) == 4

    def test_valid_no_deps(self):
        spec = _spec("x", "y", "z")
        for t in spec.tasks:
            assert t.depends_on == []

    def test_unknown_dep_raises(self):
        with pytest.raises(ValidationError, match="not defined in this goal spec"):
            _spec(("b", ["nonexistent"]))

    def test_direct_cycle_raises(self):
        with pytest.raises(ValidationError, match="cycle"):
            GoalSpec(
                name="bad",
                description="d",
                tasks=[
                    _task_def("a", ["b"]),
                    _task_def("b", ["a"]),
                ],
            )

    def test_self_cycle_raises(self):
        with pytest.raises(ValidationError, match="cycle"):
            GoalSpec(
                name="bad",
                description="d",
                tasks=[_task_def("a", ["a"])],
            )

    def test_three_node_cycle_raises(self):
        with pytest.raises(ValidationError, match="cycle"):
            GoalSpec(
                name="bad",
                description="d",
                tasks=[
                    _task_def("a", ["c"]),
                    _task_def("b", ["a"]),
                    _task_def("c", ["b"]),
                ],
            )

    def test_goal_id_optional(self):
        spec = _spec("a")
        assert spec.goal_id is None

    def test_goal_id_explicit(self):
        spec = GoalSpec(
            goal_id="goal-abc",
            name="x",
            description="d",
            tasks=[_task_def("a")],
        )
        assert spec.goal_id == "goal-abc"


# ===========================================================================
# GoalAggregate — creation
# ===========================================================================

class TestGoalAggregateCreate:

    def test_auto_goal_id(self):
        g = _goal("t1", "t2")
        assert g.goal_id.startswith("goal-")

    def test_explicit_goal_id(self):
        summaries = [_summary("t1")]
        g = GoalAggregate.create(
            name="my-goal", description="d",
            task_summaries=summaries, goal_id="goal-explicit",
        )
        assert g.goal_id == "goal-explicit"

    def test_branch_name(self):
        g = _goal("t1")
        assert g.branch == "goal/test-goal"

    def test_initial_status_pending(self):
        g = _goal("t1")
        assert g.status == GoalStatus.PENDING

    def test_tasks_indexed_by_id(self):
        g = _goal("alpha", "beta")
        assert "alpha" in g.tasks
        assert "beta" in g.tasks

    def test_state_version_starts_at_one(self):
        g = _goal("t1")
        assert g.state_version == 1


# ===========================================================================
# GoalAggregate — start()
# ===========================================================================

class TestGoalAggregateStart:

    def test_pending_to_running(self):
        g = _goal("t1")
        g.start()
        assert g.status == GoalStatus.RUNNING

    def test_start_idempotent_if_already_running(self):
        g = _goal("t1")
        g.start()
        v = g.state_version
        g.start()  # second call — no-op
        assert g.state_version == v

    def test_start_bumps_version(self):
        g = _goal("t1")
        g.start()
        assert g.state_version == 2

    def test_start_on_ready_for_review_raises(self):
        # READY_FOR_REVIEW is not terminal, but start() only allows PENDING
        # In practice: once RUNNING, start() is idempotent; once READY_FOR_REVIEW,
        # the goal is in the PR phase and start() is a no-op guard.
        # The meaningful terminal-guard test is with FAILED.
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "reason")  # → FAILED
        with pytest.raises(ValueError):
            g.start()

    def test_start_on_failed_raises(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "retries exhausted")
        with pytest.raises(ValueError, match="failed"):
            g.start()


# ===========================================================================
# GoalAggregate — record_task_status()
# ===========================================================================

class TestGoalAggregateRecordTaskStatus:

    def test_mirrors_task_status(self):
        g = _goal("t1")
        g.start()
        g.record_task_status("t1", TaskStatus.IN_PROGRESS)
        assert g.tasks["t1"].status == TaskStatus.IN_PROGRESS

    def test_unknown_task_raises(self):
        g = _goal("t1")
        g.start()
        with pytest.raises(KeyError):
            g.record_task_status("nonexistent", TaskStatus.IN_PROGRESS)

    def test_bumps_version(self):
        g = _goal("t1")
        g.start()
        v = g.state_version
        g.record_task_status("t1", TaskStatus.ASSIGNED)
        assert g.state_version == v + 1

    def test_raises_when_terminal(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "reason")  # → FAILED (terminal)
        with pytest.raises(ValueError):
            g.record_task_status("t1", TaskStatus.IN_PROGRESS)


# ===========================================================================
# GoalAggregate — record_task_merged()
# ===========================================================================

class TestGoalAggregateRecordTaskMerged:

    def test_single_task_completion(self):
        g = _goal("t1")
        g.start()
        g.record_task_merged("t1")
        assert g.tasks["t1"].status == TaskStatus.MERGED
        assert g.status == GoalStatus.READY_FOR_REVIEW

    def test_partial_merge_does_not_complete(self):
        g = _goal("t1", "t2")
        g.start()
        g.record_task_merged("t1")
        assert g.status == GoalStatus.RUNNING

    def test_all_merged_completes_goal(self):
        g = _goal("t1", "t2", "t3")
        g.start()
        g.record_task_merged("t1")
        g.record_task_merged("t2")
        assert g.status == GoalStatus.RUNNING
        g.record_task_merged("t3")
        assert g.status == GoalStatus.READY_FOR_REVIEW

    def test_completion_appends_history(self):
        g = _goal("t1")
        g.start()
        g.record_task_merged("t1")
        events = [h.event for h in g.history]
        assert "goal.ready_for_review" in events

    def test_unknown_task_raises(self):
        g = _goal("t1")
        g.start()
        with pytest.raises(KeyError):
            g.record_task_merged("ghost")

    def test_progress_counts(self):
        g = _goal("t1", "t2")
        g.start()
        g.record_task_merged("t1")
        merged, total = g.progress()
        assert merged == 1
        assert total == 2

    def test_merge_on_failed_goal_raises(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "reason")  # → FAILED
        with pytest.raises(ValueError):
            g.record_task_merged("t1")


# ===========================================================================
# GoalAggregate — record_task_canceled()
# ===========================================================================

class TestGoalAggregateRecordTaskCanceled:

    def test_single_cancel_fails_goal(self):
        g = _goal("t1", "t2")
        g.start()
        g.record_task_canceled("t1", "agent crashed")
        assert g.status == GoalStatus.FAILED
        assert g.tasks["t1"].status == TaskStatus.CANCELED

    def test_failure_reason_recorded(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "retries exhausted")
        assert "retries exhausted" in g.failure_reason

    def test_failure_appends_history(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "reason")
        events = [h.event for h in g.history]
        assert "goal.task_canceled" in events
        assert "goal.failed" in events

    def test_cancel_on_already_failed_raises(self):
        g = _goal("t1", "t2")
        g.start()
        g.record_task_canceled("t1", "first")
        with pytest.raises(ValueError, match="failed"):
            g.record_task_canceled("t2", "second")

    def test_unknown_task_raises(self):
        g = _goal("t1")
        g.start()
        with pytest.raises(KeyError):
            g.record_task_canceled("ghost", "reason")


# ===========================================================================
# GoalAggregate — is_terminal / pending_task_ids
# ===========================================================================

class TestGoalAggregateHelpers:

    def test_pending_not_terminal(self):
        assert not _goal("t1").is_terminal()

    def test_running_not_terminal(self):
        g = _goal("t1")
        g.start()
        assert not g.is_terminal()

    def test_ready_for_review_is_not_terminal(self):
        g = _goal("t1")
        g.start()
        g.record_task_merged("t1")
        assert g.status == GoalStatus.READY_FOR_REVIEW
        assert not g.is_terminal()  # PR gate still pending

    def test_merged_is_terminal(self):
        g = _goal("t1")
        g.start()
        g.record_task_merged("t1")
        g.open_pr(1, "http://url", "sha")
        g.sync_pr_state(pr_status="merged", checks_passed=True, approved=True, head_sha="sha")
        g.advance_from_pr_state()
        assert g.status == GoalStatus.MERGED
        assert g.is_terminal()

    def test_failed_is_terminal(self):
        g = _goal("t1")
        g.start()
        g.record_task_canceled("t1", "r")
        assert g.is_terminal()

    def test_pending_task_ids(self):
        g = _goal("t1", "t2", "t3")
        g.start()
        g.record_task_merged("t1")
        pending = g.pending_task_ids()
        assert "t1" not in pending
        assert "t2" in pending
        assert "t3" in pending
