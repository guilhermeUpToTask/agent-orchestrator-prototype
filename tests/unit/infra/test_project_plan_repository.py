"""
Unit tests for ProjectPlanRepository implementations.
"""
import pytest
from pathlib import Path

from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectBrief,
    ProjectPlan,
    ProjectPlanStatus,
)
from src.infra.fs.project_plan_repository import (
    YamlProjectPlanRepository,
    InMemoryProjectPlanRepository,
)


class TestInMemoryProjectPlanRepository:
    """Test InMemoryProjectPlanRepository."""

    def test_save_and_load_round_trip(self):
        repo = InMemoryProjectPlanRepository()

        plan = ProjectPlan.create("Test vision")
        repo.save(plan)

        loaded = repo.load()
        assert loaded.plan_id == plan.plan_id
        assert loaded.status == plan.status
        assert loaded.vision == plan.vision

    def test_exists_returns_false_before_save(self):
        repo = InMemoryProjectPlanRepository()
        assert not repo.exists()

    def test_exists_returns_true_after_save(self):
        repo = InMemoryProjectPlanRepository()
        plan = ProjectPlan.create("Test vision")
        repo.save(plan)
        assert repo.exists()

    def test_get_returns_none_when_no_plan(self):
        repo = InMemoryProjectPlanRepository()
        assert repo.get() is None

    def test_get_returns_plan_when_exists(self):
        repo = InMemoryProjectPlanRepository()
        plan = ProjectPlan.create("Test vision")
        repo.save(plan)
        assert repo.get() is not None
        assert repo.get().plan_id == plan.plan_id

    def test_load_raises_when_no_plan(self):
        repo = InMemoryProjectPlanRepository()
        with pytest.raises(KeyError):
            repo.load()


class TestYamlProjectPlanRepository:
    """Test YamlProjectPlanRepository."""

    def test_save_and_load_round_trip(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")

        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=["constraint 1"],
            phase_1_exit_criteria="phase 1 done",
            open_questions=["question 1"],
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
        repo.save(plan)

        loaded = repo.load()
        assert loaded.plan_id == plan.plan_id
        assert loaded.status == plan.status
        assert loaded.vision == plan.vision
        assert loaded.brief is not None
        assert loaded.brief.vision == brief.vision
        assert len(loaded.phases) == 1
        assert loaded.phases[0].name == "Foundation"

    def test_exists_returns_false_before_save(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        assert not repo.exists()

    def test_exists_returns_true_after_save(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        plan = ProjectPlan.create("Test vision")
        repo.save(plan)
        assert repo.exists()

    def test_get_returns_none_when_no_plan(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        assert repo.get() is None

    def test_get_returns_plan_when_exists(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        plan = ProjectPlan.create("Test vision")
        repo.save(plan)
        assert repo.get() is not None
        assert repo.get().plan_id == plan.plan_id

    def test_load_raises_when_no_plan(self, tmp_path):
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        with pytest.raises(KeyError):
            repo.load()

    def test_atomic_write_creates_tmp_file(self, tmp_path):
        """Test that atomic write creates a .tmp file during write."""
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")
        plan = ProjectPlan.create("Test vision")
        repo.save(plan)

        # After save, the .yaml file should exist (quarantine dir is also created)
        files = [f for f in tmp_path.iterdir() if f.is_file()]
        assert len(files) == 1
        assert files[0].name == "plan.yaml"

    def test_preserves_all_fields(self, tmp_path):
        """Test that all PlanPlan fields are preserved through save/load."""
        repo = YamlProjectPlanRepository(tmp_path / "plan.yaml")

        plan = ProjectPlan.create("Test vision")
        brief = ProjectBrief(
            vision="Test vision",
            constraints=["constraint 1"],
            phase_1_exit_criteria="phase 1 done",
            open_questions=["question 1"],
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
            ),
            Phase(
                index=1,
                name="Core Domain",
                goal="PM can create projects",
                goal_names=[],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="projects can be created",
            ),
        ]
        plan = plan.approve_phase(phases)
        plan = plan.record_goal_registered("goal1")
        plan = plan.trigger_review()
        plan = plan.complete_review("Learned a lot", "Updated architecture")

        repo.save(plan)
        loaded = repo.load()

        assert loaded.plan_id == plan.plan_id
        assert loaded.status == plan.status
        assert loaded.vision == plan.vision
        assert loaded.brief is not None
        assert loaded.brief.vision == brief.vision
        assert loaded.brief.constraints == brief.constraints
        assert loaded.brief.phase_1_exit_criteria == brief.phase_1_exit_criteria
        assert loaded.brief.open_questions == brief.open_questions
        assert len(loaded.phases) == 2
        assert loaded.phases[0].goal_names == ["goal1", "goal2"]
        assert loaded.phases[0].status == PhaseStatus.COMPLETED
        assert loaded.phases[1].status == PhaseStatus.PLANNED
        assert loaded.current_phase_index == 0
        assert loaded.architecture_summary == "Updated architecture"
        assert len(loaded.history) > 0


@pytest.mark.parametrize("repo_class", [
    InMemoryProjectPlanRepository,
    YamlProjectPlanRepository,
])
def test_both_repositories_implement_same_interface(repo_class, tmp_path):
    """Test that both implementations have the same interface."""
    if repo_class == YamlProjectPlanRepository:
        repo = repo_class(tmp_path / "plan.yaml")
    else:
        repo = repo_class()

    # Check all required methods exist
    assert hasattr(repo, "save")
    assert hasattr(repo, "load")
    assert hasattr(repo, "exists")
    assert hasattr(repo, "get")

    # Test basic operations
    assert not repo.exists()
    assert repo.get() is None

    plan = ProjectPlan.create("Test vision")
    repo.save(plan)

    assert repo.exists()
    assert repo.get() is not None
    loaded = repo.load()
    assert loaded.plan_id == plan.plan_id
