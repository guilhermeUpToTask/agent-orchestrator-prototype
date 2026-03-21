"""
tests/unit/infra/goal/test_goal_infra.py

Tests for:
  - YamlGoalRepository: save/load/update_if_version/list_all/quarantine
  - load_goal_file: happy path, missing file, invalid schema, cycle detection
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.value_objects.status import TaskStatus


# ===========================================================================
# YamlGoalRepository
# ===========================================================================

class TestYamlGoalRepository:
    @pytest.fixture()
    def repo(self, tmp_path):
        from src.infra.fs.goal_repository import YamlGoalRepository
        return YamlGoalRepository(goals_dir=tmp_path)

    def _goal(self, goal_id: str = "goal-001", name: str = "test-goal") -> GoalAggregate:
        return GoalAggregate.create(
            goal_id=goal_id,
            name=name,
            description="a test goal",
            task_summaries=[
                TaskSummary(
                    task_id="t1",
                    title="T1",
                    status=TaskStatus.CREATED,
                    branch=f"goal/{name}/task/t1",
                ),
            ],
        )

    def test_save_and_load_roundtrip(self, repo):
        g = self._goal()
        repo.save(g)
        loaded = repo.load("goal-001")
        assert loaded.goal_id == "goal-001"
        assert loaded.name == "test-goal"
        assert loaded.status == GoalStatus.PENDING

    def test_load_raises_for_missing_goal(self, repo):
        with pytest.raises(KeyError):
            repo.load("does-not-exist")

    def test_get_returns_none_for_missing(self, repo):
        assert repo.get("ghost") is None

    def test_list_all_returns_all(self, repo):
        repo.save(self._goal("goal-001", "g1"))
        repo.save(self._goal("goal-002", "g2"))
        goals = repo.list_all()
        ids = {g.goal_id for g in goals}
        assert ids == {"goal-001", "goal-002"}

    def test_list_all_empty(self, repo):
        assert repo.list_all() == []

    def test_update_if_version_succeeds(self, repo):
        g = self._goal()
        repo.save(g)
        fresh = repo.load("goal-001")
        expected_v = fresh.state_version
        fresh.start()
        assert repo.update_if_version("goal-001", fresh, expected_v)
        updated = repo.load("goal-001")
        assert updated.status == GoalStatus.RUNNING

    def test_update_if_version_conflict(self, repo):
        g = self._goal()
        repo.save(g)
        stale = repo.load("goal-001")
        # Simulate concurrent write
        fresh = repo.load("goal-001")
        fresh.start()
        repo.update_if_version("goal-001", fresh, fresh.state_version - 1)
        # stale has old version — should fail
        stale.start()
        result = repo.update_if_version("goal-001", stale, stale.state_version - 1)
        assert not result

    def test_update_if_version_raises_for_missing(self, repo):
        g = self._goal()
        with pytest.raises(KeyError):
            repo.update_if_version("goal-999", g, 1)

    def test_list_all_quarantines_corrupt_file(self, repo, tmp_path):
        # Write a corrupt YAML file
        bad_file = tmp_path / "goal-corrupt.yaml"
        bad_file.write_text(":::: not valid yaml ::::")
        goals = repo.list_all()
        # Corrupt file is quarantined, not raised
        assert all(g.goal_id != "goal-corrupt" for g in goals)
        assert (tmp_path / "quarantine" / "goal-corrupt.yaml").exists()

    def test_atomic_write_creates_file(self, repo, tmp_path):
        g = self._goal()
        repo.save(g)
        assert (tmp_path / "goal-001.yaml").exists()

    def test_full_lifecycle_roundtrip(self, repo):
        """PENDING → RUNNING → task merged → COMPLETED persists correctly."""
        g = self._goal()
        repo.save(g)

        # Start
        g2 = repo.load("goal-001")
        v = g2.state_version
        g2.start()
        assert repo.update_if_version("goal-001", g2, v)

        # Merge task
        g3 = repo.load("goal-001")
        v = g3.state_version
        g3.record_task_merged("t1")
        assert repo.update_if_version("goal-001", g3, v)

        final = repo.load("goal-001")
        assert final.status == GoalStatus.READY_FOR_REVIEW
        assert final.tasks["t1"].status == TaskStatus.MERGED


# ===========================================================================
# load_goal_file
# ===========================================================================

class TestLoadGoalFile:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "goal.yaml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_valid_file_parses(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        p = self._write(tmp_path, """
            name: my-feature
            description: Add feature X
            tasks:
              - task_id: step-1
                title: First step
                description: Do something
                capability: coding
                files_allowed_to_modify: [src/foo.py]
              - task_id: step-2
                title: Second step
                description: Do something else
                capability: coding
                files_allowed_to_modify: [src/bar.py]
                depends_on: [step-1]
        """)
        spec = load_goal_file(p)
        assert spec.name == "my-feature"
        assert len(spec.tasks) == 2

    def test_missing_file_raises(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        with pytest.raises(FileNotFoundError):
            load_goal_file(tmp_path / "nonexistent.yaml")

    def test_invalid_schema_raises(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        from pydantic import ValidationError
        p = self._write(tmp_path, """
            name: bad
            tasks:
              - task_id: x
                # missing required fields: title, description, capability
        """)
        with pytest.raises(ValidationError):
            load_goal_file(p)

    def test_cyclic_deps_raises(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        from pydantic import ValidationError
        p = self._write(tmp_path, """
            name: cyclic
            description: bad
            tasks:
              - task_id: a
                title: A
                description: d
                capability: coding
                depends_on: [b]
              - task_id: b
                title: B
                description: d
                capability: coding
                depends_on: [a]
        """)
        with pytest.raises(ValidationError, match="cycle"):
            load_goal_file(p)

    def test_unknown_depends_on_raises(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        from pydantic import ValidationError
        p = self._write(tmp_path, """
            name: bad-deps
            description: bad
            tasks:
              - task_id: a
                title: A
                description: d
                capability: coding
                depends_on: [ghost-task]
        """)
        with pytest.raises(ValidationError, match="not defined"):
            load_goal_file(p)

    def test_explicit_goal_id_preserved(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        p = self._write(tmp_path, """
            goal_id: goal-explicit-123
            name: named-goal
            description: d
            tasks:
              - task_id: t1
                title: T
                description: d
                capability: coding
        """)
        spec = load_goal_file(p)
        assert spec.goal_id == "goal-explicit-123"

    def test_no_goal_id_is_none(self, tmp_path):
        from src.infra.goal_file import load_goal_file
        p = self._write(tmp_path, """
            name: unnamed
            description: d
            tasks:
              - task_id: t1
                title: T
                description: d
                capability: coding
        """)
        spec = load_goal_file(p)
        assert spec.goal_id is None
