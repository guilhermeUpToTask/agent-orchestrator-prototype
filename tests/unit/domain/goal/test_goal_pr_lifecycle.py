"""
tests/unit/domain/goal/test_goal_pr_lifecycle.py

Unit tests for the GitHub PR-driven GoalAggregate state machine.

Coverage:
  - Full happy-path lifecycle: PENDING → RUNNING → READY_FOR_REVIEW
      → AWAITING_PR_APPROVAL → APPROVED → MERGED
  - Regression detection: new commits reset APPROVED → AWAITING_PR_APPROVAL
  - PR closed without merge → FAILED
  - Task cancellation → FAILED (backward compat)
  - open_pr() invariants (wrong status, duplicate call)
  - sync_pr_state() with no prior open_pr → ValueError
  - advance_from_pr_state() idempotency
  - is_terminal(), is_pr_phase(), needs_next_goal_unlock() queries
  - CIConfig gate evaluation: all_required_checks_passed, meets_approval_gate
  - ProjectSpec ci block serialisation round-trip
"""
from __future__ import annotations

import pytest

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.project_spec.value_objects import CIConfig
from src.domain.value_objects.pr import (
    CheckRunResult,
    PRCheckConclusion,
    PRInfo,
    PRStatus,
)
from src.domain.value_objects.status import TaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _task(task_id: str = "t1", status: TaskStatus = TaskStatus.CREATED) -> TaskSummary:
    return TaskSummary(
        task_id=task_id,
        title=f"Task {task_id}",
        status=status,
        branch=f"goal/feat/task/{task_id}",
    )


def _pending_goal(name: str = "feat", tasks: list[TaskSummary] | None = None) -> GoalAggregate:
    return GoalAggregate.create(
        name=name,
        description="My feature goal",
        task_summaries=tasks or [_task("t1")],
    )


def _ready_for_review_goal() -> GoalAggregate:
    """Returns a goal where all tasks are merged → READY_FOR_REVIEW."""
    goal = _pending_goal()
    goal.start()
    goal.record_task_merged("t1")
    return goal


def _awaiting_pr_goal(pr_number: int = 42) -> GoalAggregate:
    """Returns a goal at AWAITING_PR_APPROVAL."""
    goal = _ready_for_review_goal()
    goal.open_pr(pr_number, f"https://github.com/x/y/pull/{pr_number}", "sha-0001")
    return goal


# ---------------------------------------------------------------------------
# 1. Full lifecycle: PENDING → MERGED
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_pending_to_running(self):
        goal = _pending_goal()
        assert goal.status == GoalStatus.PENDING
        goal.start()
        assert goal.status == GoalStatus.RUNNING

    def test_start_is_idempotent(self):
        goal = _pending_goal()
        goal.start()
        v = goal.state_version
        goal.start()  # second call: no-op
        assert goal.status == GoalStatus.RUNNING
        assert goal.state_version == v

    def test_all_tasks_merged_transitions_to_ready_for_review(self):
        goal = _pending_goal(tasks=[_task("t1"), _task("t2")])
        goal.start()
        goal.record_task_merged("t1")
        assert goal.status == GoalStatus.RUNNING  # still one pending
        goal.record_task_merged("t2")
        assert goal.status == GoalStatus.READY_FOR_REVIEW

    def test_open_pr_transitions_to_awaiting(self):
        goal = _ready_for_review_goal()
        goal.open_pr(42, "https://github.com/x/y/pull/42", "sha-abc")
        assert goal.status == GoalStatus.AWAITING_PR_APPROVAL
        assert goal.pr_number == 42
        assert goal.pr_html_url == "https://github.com/x/y/pull/42"
        assert goal.pr_head_sha == "sha-abc"
        assert goal.pr_status == "open"
        assert not goal.pr_checks_passed
        assert not goal.pr_approved

    def test_full_happy_path_to_merged(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(
            pr_status="open", checks_passed=True, approved=True,
            head_sha="sha-0001", approval_count=1,
        )
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.APPROVED

        goal.sync_pr_state(
            pr_status="merged", checks_passed=True, approved=True,
            head_sha="sha-0001", approval_count=1,
        )
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.MERGED
        assert goal.is_terminal()

    def test_state_version_increments_on_each_mutation(self):
        goal = _pending_goal()
        v0 = goal.state_version
        goal.start()
        goal.record_task_merged("t1")
        goal.open_pr(1, "http://url", "sha")
        assert goal.state_version > v0 + 2


# ---------------------------------------------------------------------------
# 2. Regression coverage moved to tests/regression/domain/goal/test_goal_pr_lifecycle.py
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3. Failure paths
# ---------------------------------------------------------------------------

class TestFailurePaths:
    def test_pr_closed_unmerged_from_awaiting(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="closed", checks_passed=False, approved=False,
                           head_sha="sha-0001")
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.FAILED
        assert "closed" in goal.failure_reason.lower()
        assert goal.is_terminal()

    def test_pr_closed_unmerged_from_approved(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.APPROVED

        goal.sync_pr_state(pr_status="closed", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.FAILED

    def test_task_canceled_fails_goal_immediately(self):
        goal = _pending_goal(tasks=[_task("t1"), _task("t2")])
        goal.start()
        goal.record_task_canceled("t1", "max retries exceeded")
        assert goal.status == GoalStatus.FAILED
        assert "t1" in goal.failure_reason
        assert goal.is_terminal()

    def test_no_mutations_allowed_after_failed(self):
        goal = _pending_goal()
        goal.start()
        goal.record_task_canceled("t1", "error")
        with pytest.raises(ValueError, match="already failed"):
            goal.start()

    def test_no_mutations_allowed_after_merged(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="merged", checks_passed=True,
                           approved=True, head_sha="sha")
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.MERGED
        with pytest.raises(ValueError):
            goal.record_task_status("t1", TaskStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# 4. open_pr() invariants
# ---------------------------------------------------------------------------

class TestOpenPRInvariants:
    def test_open_pr_requires_ready_for_review(self):
        goal = _pending_goal()
        goal.start()
        with pytest.raises(ValueError, match="ready_for_review"):
            goal.open_pr(1, "http://url", "sha")

    def test_open_pr_from_running_raises(self):
        goal = _pending_goal()
        goal.start()
        with pytest.raises(ValueError):
            goal.open_pr(1, "http://url", "sha")

    def test_open_pr_duplicate_raises(self):
        # After open_pr() succeeds, the goal is AWAITING_PR_APPROVAL,
        # so the status guard fires first (not the pr_number guard).
        goal = _ready_for_review_goal()
        goal.open_pr(1, "http://url", "sha")
        assert goal.status == GoalStatus.AWAITING_PR_APPROVAL
        with pytest.raises(ValueError, match="ready_for_review"):
            goal.open_pr(2, "http://other", "sha2")

    def test_open_pr_records_history_entry(self):
        goal = _ready_for_review_goal()
        goal.open_pr(99, "http://url", "sha")
        events = [h.event for h in goal.history]
        assert "goal.pr_opened" in events


# ---------------------------------------------------------------------------
# 5. sync_pr_state() invariants
# ---------------------------------------------------------------------------

class TestSyncPRStateInvariants:
    def test_sync_without_pr_number_raises(self):
        goal = _ready_for_review_goal()  # no PR yet
        with pytest.raises(ValueError, match="no PR"):
            goal.sync_pr_state(pr_status="open", checks_passed=True,
                               approved=True, head_sha="sha")

    def test_sync_on_terminal_goal_records_history_but_no_status_change(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="closed", checks_passed=False,
                           approved=False, head_sha="sha")
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.FAILED
        # sync on terminal goal → allowed as data update, status stays FAILED
        goal.sync_pr_state(pr_status="closed", checks_passed=False,
                           approved=False, head_sha="sha")
        assert goal.status == GoalStatus.FAILED


# ---------------------------------------------------------------------------
# 6. advance_from_pr_state() idempotency
# ---------------------------------------------------------------------------

class TestAdvanceIdempotency:
    def test_advance_without_pr_number_is_noop(self):
        goal = _ready_for_review_goal()
        v = goal.state_version
        goal.advance_from_pr_state()
        assert goal.state_version == v

    def test_advance_with_no_eligible_transition_is_noop(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="open", checks_passed=False,
                           approved=False, head_sha="sha-0001")
        goal.advance_from_pr_state()
        # No checks yet — status unchanged
        assert goal.status == GoalStatus.AWAITING_PR_APPROVAL

    def test_double_advance_does_not_double_transition(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="open", checks_passed=True,
                           approved=True, head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.APPROVED
        v = goal.state_version
        goal.advance_from_pr_state()  # second call
        assert goal.status == GoalStatus.APPROVED
        assert goal.state_version == v  # no extra bump


# ---------------------------------------------------------------------------
# 7. Domain queries
# ---------------------------------------------------------------------------

class TestDomainQueries:
    def test_is_pr_phase(self):
        goal = _pending_goal()
        assert not goal.is_pr_phase()
        goal.start()
        assert not goal.is_pr_phase()
        goal.record_task_merged("t1")
        assert goal.is_pr_phase()   # READY_FOR_REVIEW
        goal.open_pr(1, "u", "s")
        assert goal.is_pr_phase()   # AWAITING_PR_APPROVAL

    def test_needs_next_goal_unlock_false_until_approved(self):
        goal = _awaiting_pr_goal()   # head_sha = "sha-0001"
        assert not goal.needs_next_goal_unlock()
        # Must pass the SAME sha as open_pr to avoid regression detection
        goal.sync_pr_state(pr_status="open", checks_passed=True,
                           approved=True, head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        assert goal.needs_next_goal_unlock()  # APPROVED

    def test_needs_next_goal_unlock_true_when_merged(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="merged", checks_passed=True,
                           approved=True, head_sha="sha", approval_count=1)
        goal.advance_from_pr_state()
        assert goal.needs_next_goal_unlock()

    def test_progress_returns_correct_counts(self):
        goal = _pending_goal(tasks=[_task("t1"), _task("t2"), _task("t3")])
        goal.start()
        goal.record_task_merged("t1")
        goal.record_task_merged("t2")
        merged, total = goal.progress()
        assert merged == 2
        assert total == 3


# ---------------------------------------------------------------------------
# 8. CIConfig gate evaluation
# ---------------------------------------------------------------------------

class TestCIConfigGate:
    def _pr_info(
        self,
        check_names_and_conclusions: list[tuple[str, str]],
        approval_count: int = 0,
        changes_requested: bool = False,
        status: PRStatus = PRStatus.OPEN,
    ) -> PRInfo:
        return PRInfo(
            pr_number=1,
            status=status,
            head_branch="goal/feat",
            base_branch="main",
            head_sha="abc",
            html_url="http://url",
            title="feat",
            check_runs=[
                CheckRunResult(name=n, conclusion=PRCheckConclusion(c))
                for n, c in check_names_and_conclusions
            ],
            approval_count=approval_count,
            changes_requested=changes_requested,
        )

    def test_all_required_checks_passed_when_all_success(self):
        pr = self._pr_info([("tests", "success"), ("lint", "success")])
        assert pr.all_required_checks_passed(["tests", "lint"])

    def test_fails_if_required_check_missing(self):
        pr = self._pr_info([("tests", "success")])
        assert not pr.all_required_checks_passed(["tests", "lint"])

    def test_fails_if_required_check_failed(self):
        pr = self._pr_info([("tests", "failure"), ("lint", "success")])
        assert not pr.all_required_checks_passed(["tests", "lint"])

    def test_skipped_counts_as_passed(self):
        pr = self._pr_info([("tests", "success"), ("lint", "skipped")])
        assert pr.all_required_checks_passed(["tests", "lint"])

    def test_pending_check_fails_gate(self):
        pr = self._pr_info([("tests", "success"), ("lint", "pending")])
        assert not pr.all_required_checks_passed(["tests", "lint"])

    def test_no_required_checks_always_passes(self):
        pr = self._pr_info([])
        assert pr.all_required_checks_passed([])

    def test_meets_approval_gate(self):
        pr = self._pr_info([], approval_count=1)
        assert pr.meets_approval_gate(1)

    def test_not_enough_approvals(self):
        pr = self._pr_info([], approval_count=0)
        assert not pr.meets_approval_gate(1)

    def test_changes_requested_blocks_gate(self):
        pr = self._pr_info([], approval_count=2, changes_requested=True)
        assert not pr.meets_approval_gate(1)

    def test_zero_min_approvals_always_passes(self):
        pr = self._pr_info([], approval_count=0)
        assert pr.meets_approval_gate(0)


# ---------------------------------------------------------------------------
# 9. ProjectSpec.ci serialisation round-trip
# ---------------------------------------------------------------------------

class TestProjectSpecCISerialization:
    def test_ci_block_survives_round_trip(self):
        spec = ProjectSpec.create(
            name="myproject",
            objective_description="Test project",
            objective_domain="testing",
            ci_required_checks=["tests", "lint", "build"],
            ci_min_approvals=2,
        )
        data = spec.to_dict()
        assert data["ci"]["required_checks"] == ["tests", "lint", "build"]
        assert data["ci"]["min_approvals"] == 2

        spec2 = ProjectSpec.from_dict(data)
        assert list(spec2.ci.required_checks) == ["tests", "lint", "build"]
        assert spec2.ci.min_approvals == 2

    def test_ci_block_optional_defaults_to_no_gate(self):
        spec = ProjectSpec.create(
            name="nogate",
            objective_description="No gate",
            objective_domain="testing",
        )
        assert spec.ci.required_checks == ()
        assert spec.ci.min_approvals == 0

    def test_missing_ci_block_in_yaml_defaults_gracefully(self):
        data = ProjectSpec.create("p", "d", "d").to_dict()
        data.pop("ci", None)  # simulate old YAML without ci block
        spec = ProjectSpec.from_dict(data)
        assert spec.ci.min_approvals == 0
        assert spec.ci.required_checks == ()

    def test_ci_config_no_gate_factory(self):
        cfg = CIConfig.no_gate()
        assert cfg.required_checks == ()
        assert cfg.min_approvals == 0

    def test_ci_config_is_check_required(self):
        cfg = CIConfig(required_checks=["tests", "lint"], min_approvals=1)
        assert cfg.is_check_required("tests")
        assert cfg.is_check_required("lint")
        assert not cfg.is_check_required("build")
