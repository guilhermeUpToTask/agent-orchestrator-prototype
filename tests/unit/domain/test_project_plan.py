"""
Unit tests for ProjectPlan aggregate.
"""
from datetime import datetime, timezone

import pytest

from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectBrief,
    ProjectPlan,
    ProjectPlanStatus,
)


class TestProjectPlanLifecycle:
    """Test ProjectPlan state machine and transitions."""

    def test_create_creates_discovery_plan(self):
        plan = ProjectPlan.create("Test vision")
        assert plan.status == ProjectPlanStatus.DISCOVERY
        assert plan.vision == "Test vision"
        assert plan.brief is None
        assert plan.current_phase_index == -1
        assert len(plan.phases) == 0

    def test_approve_brief_transitions_to_architecture(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=["constraint 1"],
            phase_1_exit_criteria="phase 1 done",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        assert plan.status == ProjectPlanStatus.ARCHITECTURE
        assert plan.brief == brief

    def test_approve_brief_requires_discovery_status(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        # Try to approve brief again (status is now ARCHITECTURE)
        with pytest.raises(ValueError, match="expected one of"):
            plan.approve_brief(brief)

    def test_approve_phase_transitions_to_phase_active(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=[],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        assert plan.status == ProjectPlanStatus.PHASE_ACTIVE
        assert plan.current_phase_index == 0
        assert plan.phases[0].status == PhaseStatus.ACTIVE

    def test_approve_phase_requires_architecture_or_review(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=[],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)

        # Try to approve phase again (status is now PHASE_ACTIVE)
        with pytest.raises(ValueError, match="expected one of"):
            plan.approve_phase(phases)

    def test_record_goal_registered_appends_to_phase(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=[],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        plan = plan.record_goal_registered("setup-auth")

        assert "setup-auth" in plan.phases[0].goal_names

    def test_trigger_review_transitions_to_phase_review(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=["goal1", "goal2"],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        plan = plan.trigger_review()

        assert plan.status == ProjectPlanStatus.PHASE_REVIEW
        assert plan.phases[0].status == PhaseStatus.COMPLETED

    def test_trigger_review_requires_phase_active(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        # Status is ARCHITECTURE, not PHASE_ACTIVE
        with pytest.raises(ValueError, match="expected one of"):
            plan.trigger_review()

    def test_complete_review_records_lessons(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=["goal1"],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        plan = plan.trigger_review()
        plan = plan.complete_review(
            lessons="Learned a lot",
            architecture_summary="Updated architecture",
        )

        assert plan.phases[0].lessons == "Learned a lot"
        assert plan.architecture_summary == "Updated architecture"
        assert plan.status == ProjectPlanStatus.PHASE_REVIEW

    def test_mark_done_transitions_to_done(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=["goal1"],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        plan = plan.trigger_review()
        plan = plan.complete_review(lessons="", architecture_summary="")
        plan = plan.mark_done()

        assert plan.status == ProjectPlanStatus.DONE
        assert plan.is_terminal()

    def test_is_terminal_only_done(self):
        plan = ProjectPlan.create("Test vision")
        assert not plan.is_terminal()

        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        assert not plan.is_terminal()

        # Create proper flow to reach DONE status
        plan = ProjectPlan.create("Test vision")
        plan = plan.approve_brief(brief)
        phases = [Phase(0, "Test", "goal", [], PhaseStatus.PLANNED, "", "")]
        plan = plan.approve_phase(phases)
        plan = plan.trigger_review()
        plan = plan.complete_review("", "")
        plan = plan.mark_done()
        assert plan.is_terminal()

    def test_state_version_increments_on_mutations(self):
        plan = ProjectPlan.create("Test vision")
        initial_version = plan.state_version

        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        assert plan.state_version == initial_version + 1

    def test_current_phase_returns_active_phase(self):
        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)

        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=[],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="user can login",
            )
        ]
        plan = plan.approve_phase(phases)
        current = plan.current_phase()
        assert current is not None
        assert current.index == 0
        assert current.status == PhaseStatus.ACTIVE


class TestPhase:
    """Test Phase value object."""

    def test_with_status_returns_new_phase(self):
        phase = Phase(
            index=0,
            name="Foundation",
            goal="Auth system working",
            goal_names=[],
            status=PhaseStatus.PLANNED,
            lessons="",
            exit_criteria="user can login",
        )
        new_phase = phase.with_status(PhaseStatus.ACTIVE)
        assert new_phase.status == PhaseStatus.ACTIVE
        assert new_phase.index == phase.index
        assert new_phase.name == phase.name

    def test_register_goal_appends_to_goal_names(self):
        phase = Phase(
            index=0,
            name="Foundation",
            goal="Auth system working",
            goal_names=["goal1"],
            status=PhaseStatus.PLANNED,
            lessons="",
            exit_criteria="user can login",
        )
        new_phase = phase.register_goal("goal2")
        assert "goal2" in new_phase.goal_names
        assert "goal1" in new_phase.goal_names
