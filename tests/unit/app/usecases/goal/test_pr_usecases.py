"""
tests/unit/app/usecases/goal/test_pr_usecases.py

Unit tests for the three PR-driven goal use cases:
  - CreateGoalPRUseCase
  - SyncGoalPRStatusUseCase
  - AdvanceGoalFromPRUseCase

All tests use StubGitHubClient and MagicMock repositories.
No I/O, no Redis, no real GitHub API calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call

from src.app.usecases.advance_goal_from_pr import AdvanceGoalFromPRUseCase
from src.app.usecases.create_goal_pr import CreateGoalPRUseCase
from src.app.usecases.sync_goal_pr_status import SyncGoalPRStatusUseCase
from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.value_objects.status import TaskStatus
from src.infra.github.client import StubGitHubClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_goal(status: GoalStatus = GoalStatus.READY_FOR_REVIEW) -> GoalAggregate:
    task = TaskSummary(
        task_id="t1", title="Task", status=TaskStatus.MERGED, branch="goal/feat/task/t1"
    )
    g = GoalAggregate.create("feat", "My feature", [task])
    if status in (GoalStatus.RUNNING, GoalStatus.READY_FOR_REVIEW,
                  GoalStatus.AWAITING_PR_APPROVAL, GoalStatus.APPROVED, GoalStatus.MERGED):
        g.start()
    if status in (GoalStatus.READY_FOR_REVIEW, GoalStatus.AWAITING_PR_APPROVAL,
                  GoalStatus.APPROVED, GoalStatus.MERGED):
        g.record_task_merged("t1")
    if status in (GoalStatus.AWAITING_PR_APPROVAL, GoalStatus.APPROVED, GoalStatus.MERGED):
        g.open_pr(7, "https://github.com/x/y/pull/7", "sha-0001")
    if status == GoalStatus.APPROVED:
        g.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                        head_sha="sha-0001", approval_count=1)
        g.advance_from_pr_state()
    if status == GoalStatus.MERGED:
        g.sync_pr_state(pr_status="merged", checks_passed=True, approved=True,
                        head_sha="sha-0001", approval_count=1)
        g.advance_from_pr_state()
    return g


def _mock_repo(goal: GoalAggregate) -> MagicMock:
    """CAS mock: load always returns the same goal; update always succeeds."""
    repo = MagicMock()
    repo.load.return_value = goal
    repo.get.return_value = goal
    repo.update_if_version.return_value = True
    return repo


def _mock_events() -> MagicMock:
    return MagicMock()


def _default_spec() -> ProjectSpec:
    return ProjectSpec.create(
        "proj", "desc", "domain",
        ci_required_checks=["tests", "lint"],
        ci_min_approvals=1,
    )


# ---------------------------------------------------------------------------
# CreateGoalPRUseCase
# ---------------------------------------------------------------------------

class TestCreateGoalPRUseCase:
    def _uc(self, goal: GoalAggregate, github: StubGitHubClient | None = None,
            base: str = "main") -> CreateGoalPRUseCase:
        return CreateGoalPRUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
            github=github or StubGitHubClient(),
            base_branch=base,
        )

    def test_creates_pr_and_transitions_goal_to_awaiting(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        uc = self._uc(goal)
        pr_num = uc.execute(goal.goal_id)
        assert pr_num >= 1
        assert goal.status == GoalStatus.AWAITING_PR_APPROVAL
        assert goal.pr_number == pr_num

    def test_pr_head_branch_matches_goal_branch(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        github = StubGitHubClient()
        uc = self._uc(goal, github)
        pr_num = uc.execute(goal.goal_id)
        assert github._prs[pr_num]["head_branch"] == goal.branch

    def test_pr_base_branch_is_main_by_default(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        github = StubGitHubClient()
        uc = self._uc(goal, github)
        pr_num = uc.execute(goal.goal_id)
        assert github._prs[pr_num]["base_branch"] == "main"

    def test_pr_title_includes_goal_name(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        github = StubGitHubClient()
        uc = self._uc(goal, github)
        pr_num = uc.execute(goal.goal_id)
        assert "feat" in github._prs[pr_num]["title"]

    def test_emits_goal_pr_opened_event(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        events = _mock_events()
        uc = CreateGoalPRUseCase(
            goal_repo=_mock_repo(goal),
            event_port=events,
            github=StubGitHubClient(),
        )
        uc.execute(goal.goal_id)
        events.publish.assert_called_once()
        published_event = events.publish.call_args[0][0]
        assert published_event.type == "goal.pr_opened"
        assert published_event.payload["goal_id"] == goal.goal_id

    def test_raises_if_goal_not_ready_for_review(self):
        goal = _make_goal(GoalStatus.RUNNING)
        uc = self._uc(goal)
        with pytest.raises(ValueError, match="ready_for_review"):
            uc.execute(goal.goal_id)

    def test_raises_if_goal_already_awaiting(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        uc = self._uc(goal)
        with pytest.raises((ValueError, Exception)):
            uc.execute(goal.goal_id)

    def test_history_records_pr_opened(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        uc = self._uc(goal)
        uc.execute(goal.goal_id)
        events = [h.event for h in goal.history]
        assert "goal.pr_opened" in events


# ---------------------------------------------------------------------------
# SyncGoalPRStatusUseCase
# ---------------------------------------------------------------------------

class TestSyncGoalPRStatusUseCase:
    def _uc(self, goal: GoalAggregate, github: StubGitHubClient,
            spec: ProjectSpec | None = None) -> SyncGoalPRStatusUseCase:
        return SyncGoalPRStatusUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
            github=github,
            spec=spec or _default_spec(),
        )

    def _goal_with_pr(self) -> tuple[GoalAggregate, StubGitHubClient]:
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        github = StubGitHubClient()
        # Create the PR in the stub first
        CreateGoalPRUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
            github=github,
        ).execute(goal.goal_id)
        return goal, github

    def test_returns_true_when_synced(self):
        goal, github = self._goal_with_pr()
        uc = self._uc(goal, github)
        assert uc.execute(goal.goal_id) is True

    def test_sets_checks_passed_when_all_required_green(self):
        goal, github = self._goal_with_pr()
        github.pass_all_checks(goal.pr_number, ["tests", "lint"])
        github.approve_pr(goal.pr_number)
        uc = self._uc(goal, github)
        uc.execute(goal.goal_id)
        assert goal.pr_checks_passed
        assert goal.pr_approved

    def test_leaves_checks_passed_false_when_checks_missing(self):
        goal, github = self._goal_with_pr()
        # Only one check present
        github.pass_all_checks(goal.pr_number, ["tests"])
        uc = self._uc(goal, github)
        uc.execute(goal.goal_id)
        assert not goal.pr_checks_passed  # "lint" missing

    def test_skips_goals_not_in_pr_phase(self):
        goal = _make_goal(GoalStatus.RUNNING)
        github = StubGitHubClient()
        uc = self._uc(goal, github)
        result = uc.execute(goal.goal_id)
        assert result is False

    def test_skips_goals_with_no_pr_number(self):
        goal = _make_goal(GoalStatus.READY_FOR_REVIEW)
        github = StubGitHubClient()
        uc = self._uc(goal, github)
        result = uc.execute(goal.goal_id)
        assert result is False

    def test_swallows_github_error_and_returns_false(self):
        from src.domain.ports.github import GitHubError
        goal, _ = self._goal_with_pr()
        broken = MagicMock()
        broken.get_pr_info.side_effect = GitHubError("API down", 503)
        uc = SyncGoalPRStatusUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
            github=broken,
            spec=_default_spec(),
        )
        result = uc.execute(goal.goal_id)
        assert result is False  # doesn't raise

    def test_no_ci_spec_means_no_gate(self):
        goal, github = self._goal_with_pr()
        # No spec → min_approvals=0, no required checks
        uc = SyncGoalPRStatusUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
            github=github,
            spec=None,
        )
        uc.execute(goal.goal_id)
        assert goal.pr_checks_passed  # no required checks → always passes
        assert goal.pr_approved       # min_approvals=0 → always passes

    def test_emits_pr_state_synced_event(self):
        goal, github = self._goal_with_pr()
        events = _mock_events()
        uc = SyncGoalPRStatusUseCase(
            goal_repo=_mock_repo(goal),
            event_port=events,
            github=github,
            spec=_default_spec(),
        )
        uc.execute(goal.goal_id)
        events.publish.assert_called_once()
        published = events.publish.call_args[0][0]
        assert published.type == "goal.pr_state_synced"


# ---------------------------------------------------------------------------
# AdvanceGoalFromPRUseCase
# ---------------------------------------------------------------------------

class TestAdvanceGoalFromPRUseCase:
    def _uc(self, goal: GoalAggregate) -> AdvanceGoalFromPRUseCase:
        return AdvanceGoalFromPRUseCase(
            goal_repo=_mock_repo(goal),
            event_port=_mock_events(),
        )

    def test_awaiting_plus_checks_plus_approval_gives_approved(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        uc = self._uc(goal)
        new_status = uc.execute(goal.goal_id)
        assert new_status == GoalStatus.APPROVED

    def test_approved_plus_merged_gives_merged(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        goal.sync_pr_state(pr_status="merged", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        uc = self._uc(goal)
        new_status = uc.execute(goal.goal_id)
        assert new_status == GoalStatus.MERGED

    def test_closed_pr_gives_failed(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="closed", checks_passed=False, approved=False,
                           head_sha="sha-0001")
        uc = self._uc(goal)
        new_status = uc.execute(goal.goal_id)
        assert new_status == GoalStatus.FAILED

    def test_no_eligible_transition_returns_none(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="open", checks_passed=False, approved=False,
                           head_sha="sha-0001")
        uc = self._uc(goal)
        result = uc.execute(goal.goal_id)
        assert result is None

    def test_terminal_goal_returns_none(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="closed", checks_passed=False,
                           approved=False, head_sha="sha")
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.FAILED
        uc = self._uc(goal)
        result = uc.execute(goal.goal_id)
        assert result is None

    def test_goal_not_found_returns_none(self):
        repo = MagicMock()
        repo.get.return_value = None
        uc = AdvanceGoalFromPRUseCase(goal_repo=repo, event_port=_mock_events())
        result = uc.execute("missing-goal-id")
        assert result is None

    def test_emits_goal_approved_event(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        events = _mock_events()
        uc = AdvanceGoalFromPRUseCase(goal_repo=_mock_repo(goal), event_port=events)
        uc.execute(goal.goal_id)
        events.publish.assert_called_once()
        published = events.publish.call_args[0][0]
        assert published.type == "goal.approved"

    def test_emits_goal_merged_event(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="merged", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        events = _mock_events()
        uc = AdvanceGoalFromPRUseCase(goal_repo=_mock_repo(goal), event_port=events)
        uc.execute(goal.goal_id)
        published = events.publish.call_args[0][0]
        assert published.type == "goal.merged"

    def test_emits_goal_failed_event_on_closed_pr(self):
        goal = _make_goal(GoalStatus.AWAITING_PR_APPROVAL)
        goal.sync_pr_state(pr_status="closed", checks_passed=False,
                           approved=False, head_sha="sha-0001")
        events = _mock_events()
        uc = AdvanceGoalFromPRUseCase(goal_repo=_mock_repo(goal), event_port=events)
        uc.execute(goal.goal_id)
        published = events.publish.call_args[0][0]
        assert published.type == "goal.failed"
