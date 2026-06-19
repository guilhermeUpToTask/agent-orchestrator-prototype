"""
Unit tests for DecisionEntry with SpecChanges.
"""
import pytest

from src.domain.ports.project_state import DecisionEntry, SpecChanges, apply_to_spec
from src.domain.project_spec.aggregate import ProjectSpec


class TestSpecChanges:
    """Test SpecChanges value object."""

    def test_is_empty_when_all_empty(self):
        sc = SpecChanges()
        assert sc.is_empty

    def test_is_empty_when_add_required_populated(self):
        sc = SpecChanges(add_required=["fastapi"])
        assert not sc.is_empty

    def test_is_empty_when_add_forbidden_populated(self):
        sc = SpecChanges(add_forbidden=["django"])
        assert not sc.is_empty

    def test_is_empty_when_remove_required_populated(self):
        sc = SpecChanges(remove_required=["flask"])
        assert not sc.is_empty

    def test_is_empty_when_remove_forbidden_populated(self):
        sc = SpecChanges(remove_forbidden=["old_pattern"])
        assert not sc.is_empty

    def test_all_fields_can_be_populated(self):
        sc = SpecChanges(
            add_required=["fastapi"],
            add_forbidden=["django"],
            remove_required=["flask"],
            remove_forbidden=["old_pattern"],
        )
        assert not sc.is_empty
        assert sc.add_required == ["fastapi"]
        assert sc.add_forbidden == ["django"]
        assert sc.remove_required == ["flask"]
        assert sc.remove_forbidden == ["old_pattern"]


class TestApplyToSpec:
    """Test apply_to_spec function."""

    def _create_test_spec(self) -> ProjectSpec:
        return ProjectSpec.create(
            name="test-project",
            objective_description="Test objective",
            objective_domain="test-domain",
            backend=["fastapi"],
            database=["postgres"],
            required=["fastapi"],
            forbidden=["django"],
        )

    def test_add_required_adds_to_spec(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(add_required=["redis"]),
        )
        new_spec = apply_to_spec(spec, decision)
        assert "redis" in new_spec.constraints.required
        assert "fastapi" in new_spec.constraints.required

    def test_add_forbidden_adds_to_spec(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(add_forbidden=["mysql"]),
        )
        new_spec = apply_to_spec(spec, decision)
        assert "mysql" in new_spec.constraints.forbidden
        assert "django" in new_spec.constraints.forbidden

    def test_remove_required_removes_from_spec(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(remove_required=["fastapi"]),
        )
        new_spec = apply_to_spec(spec, decision)
        assert "fastapi" not in new_spec.constraints.required

    def test_remove_forbidden_removes_from_spec(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(remove_forbidden=["django"]),
        )
        new_spec = apply_to_spec(spec, decision)
        assert "django" not in new_spec.constraints.forbidden

    def test_combined_add_and_remove(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(
                add_required=["redis"],
                remove_forbidden=["django"],
            ),
        )
        new_spec = apply_to_spec(spec, decision)
        assert "redis" in new_spec.constraints.required
        assert "django" not in new_spec.constraints.forbidden
        assert "fastapi" in new_spec.constraints.required  # unchanged

    def test_raises_when_spec_changes_is_none(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=None,
        )
        with pytest.raises(ValueError, match="has no spec_changes"):
            apply_to_spec(spec, decision)

    def test_raises_when_spec_changes_is_empty(self):
        spec = self._create_test_spec()
        decision = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=SpecChanges(),
        )
        with pytest.raises(ValueError, match="has empty spec_changes"):
            apply_to_spec(spec, decision)


class TestDecisionEntry:
    """Test DecisionEntry with spec_changes field."""

    def test_create_without_spec_changes(self):
        entry = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
        )
        assert entry.spec_changes is None

    def test_create_with_spec_changes(self):
        sc = SpecChanges(add_required=["fastapi"])
        entry = DecisionEntry(
            id="test-decision",
            date="2024-01-01",
            status="active",
            domain="backend",
            feature_tag="",
            content="Test decision",
            spec_changes=sc,
        )
        assert entry.spec_changes is not None
        assert entry.spec_changes.add_required == ["fastapi"]
