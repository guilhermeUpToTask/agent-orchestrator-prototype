"""
tests/unit/domain/project_spec/test_project_spec_domain.py

Unit tests for the ProjectSpec domain module:

  - SpecVersion value object (parsing, comparison, bump helpers)
  - ProjectSpec aggregate construction and invariant enforcement
  - ProjectSpec query methods (is_allowed_dependency, is_forbidden, etc.)
  - Controlled mutation via _apply_approved_change()
  - Serialisation round-trip (to_dict / from_dict)
"""
from __future__ import annotations

import pytest

from src.domain.project_spec.value_objects import (
    SpecVersion,
)
from src.domain.project_spec.aggregate import ProjectSpec, _SpecMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_spec(**overrides) -> ProjectSpec:
    """Return the smallest valid ProjectSpec for use in tests."""
    defaults = dict(
        name="test-project",
        objective_description="A test project for unit tests",
        objective_domain="testing",
        backend=["python"],
        database=["redis"],
        infra=["docker"],
        forbidden=["django", "flask"],
        required=["pydantic"],
        directories=[{"name": "src/domain", "purpose": "Domain layer"}],
        version="0.1.0",
    )
    defaults.update(overrides)
    return ProjectSpec.create(**defaults)


# ===========================================================================
# SpecVersion
# ===========================================================================

class TestSpecVersion:
    def test_valid_semver_accepted(self):
        v = SpecVersion(raw="1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3

    def test_initial_returns_0_1_0(self):
        assert SpecVersion.initial().raw == "0.1.0"

    def test_invalid_semver_raises(self):
        with pytest.raises(ValueError, match="semver"):
            SpecVersion(raw="1.2")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            SpecVersion(raw="v1.0.0")

    def test_bump_patch(self):
        v = SpecVersion(raw="1.2.3")
        assert v.bump_patch().raw == "1.2.4"

    def test_bump_minor_resets_patch(self):
        v = SpecVersion(raw="1.2.9")
        assert v.bump_minor().raw == "1.3.0"

    def test_bump_major_resets_minor_and_patch(self):
        v = SpecVersion(raw="1.2.3")
        assert v.bump_major().raw == "2.0.0"

    def test_comparison_lt(self):
        assert SpecVersion(raw="0.1.0") < SpecVersion(raw="0.2.0")
        assert SpecVersion(raw="1.0.0") > SpecVersion(raw="0.9.9")

    def test_equality_via_raw(self):
        assert SpecVersion(raw="1.0.0") == SpecVersion(raw="1.0.0")

    def test_str_returns_raw(self):
        assert str(SpecVersion(raw="2.3.4")) == "2.3.4"

    def test_immutability(self):
        """SpecVersion is frozen — reassignment must fail."""
        v = SpecVersion(raw="1.0.0")
        with pytest.raises(Exception):
            v.raw = "2.0.0"  # type: ignore[misc]


# ===========================================================================
# ProjectSpec construction & invariants
# ===========================================================================

class TestProjectSpecConstruction:
    def test_create_minimal_spec(self):
        spec = _minimal_spec()
        assert spec.name == "test-project"
        assert spec.meta.version == "0.1.0"

    def test_version_property_returns_spec_version_object(self):
        spec = _minimal_spec()
        assert isinstance(spec.version, SpecVersion)
        assert spec.version.major == 0

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            ProjectSpec.create(
                name="   ",
                objective_description="desc",
                objective_domain="domain",
            )

    def test_empty_objective_description_raises(self):
        with pytest.raises(ValueError, match="description must not be empty"):
            ProjectSpec.create(
                name="test",
                objective_description="",
                objective_domain="domain",
            )

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError):
            ProjectSpec.create(
                name="test",
                objective_description="desc",
                objective_domain="domain",
                version="not-semver",
            )

    def test_frozen_prevents_direct_mutation(self):
        spec = _minimal_spec()
        with pytest.raises(Exception):
            spec.meta = _SpecMeta(name="hacked", version="9.9.9")  # type: ignore

    def test_no_goals_or_tasks_in_schema(self):
        """Schema must not include goal/task fields — checked via to_dict()."""
        d = _minimal_spec().to_dict()
        forbidden_keys = {"goals", "tasks", "execution", "feature_id", "goal_id"}
        assert not forbidden_keys.intersection(d.keys())


# ===========================================================================
# Query methods
# ===========================================================================

class TestProjectSpecQueries:
    def setup_method(self):
        self.spec = _minimal_spec(
            forbidden=["django", "flask", "celery"],
            required=["pydantic", "redis"],
            directories=[
                {"name": "src/domain", "purpose": "Domain"},
                {"name": "src/infra", "purpose": "Infrastructure"},
            ],
        )

    # is_allowed_dependency ---------------------------------------------------

    def test_allowed_dependency_returns_true_for_safe_dep(self):
        assert self.spec.is_allowed_dependency("pydantic") is True

    def test_forbidden_dependency_returns_false(self):
        assert self.spec.is_allowed_dependency("django") is False

    def test_forbidden_substring_match_is_case_insensitive(self):
        assert self.spec.is_allowed_dependency("Django-REST-framework") is False

    def test_partial_match_in_dep_name(self):
        # "celery" is forbidden; "celery-beat" should also be blocked
        assert self.spec.is_allowed_dependency("celery-beat") is False

    # is_forbidden ------------------------------------------------------------

    def test_is_forbidden_exact_match(self):
        assert self.spec.is_forbidden("flask") is True

    def test_is_forbidden_path_containing_pattern(self):
        assert self.spec.is_forbidden("/some/path/celery/worker") is True

    def test_is_forbidden_safe_pattern_returns_false(self):
        assert self.spec.is_forbidden("fastapi") is False

    # is_required -------------------------------------------------------------

    def test_is_required_true_for_known_requirement(self):
        assert self.spec.is_required("pydantic") is True

    def test_is_required_false_for_unknown(self):
        assert self.spec.is_required("numpy") is False

    # directory helpers -------------------------------------------------------

    def test_has_directory_true_for_declared(self):
        assert self.spec.has_directory("src/domain") is True

    def test_has_directory_false_for_undeclared(self):
        assert self.spec.has_directory("src/nonexistent") is False

    def test_directory_purpose_returns_string(self):
        assert self.spec.directory_purpose("src/domain") == "Domain"

    def test_directory_purpose_returns_none_for_unknown(self):
        assert self.spec.directory_purpose("src/unknown") is None

    # get_architecture_constraints --------------------------------------------

    def test_get_architecture_constraints_shape(self):
        ac = self.spec.get_architecture_constraints()
        assert "project" in ac
        assert "tech_stack" in ac
        assert "constraints" in ac
        assert "structure" in ac
        assert isinstance(ac["constraints"]["forbidden"], list)
        assert isinstance(ac["structure"], list)

    def test_architecture_constraints_are_copies(self):
        """Modifying the returned dict must not affect the aggregate."""
        ac = self.spec.get_architecture_constraints()
        ac["constraints"]["forbidden"].append("INJECTED")
        assert "INJECTED" not in self.spec.constraints.forbidden


# ===========================================================================
# validate_structure
# ===========================================================================

class TestValidateStructure:
    def test_clean_spec_returns_empty_violations(self):
        spec = _minimal_spec()
        assert spec.validate_structure() == []

    def test_overlapping_forbidden_and_required_reported(self):
        spec = _minimal_spec(forbidden=["pydantic"], required=["pydantic"])
        violations = spec.validate_structure()
        assert any("both forbidden and required" in v for v in violations)

    def test_duplicate_directory_reported(self):
        spec = ProjectSpec.create(
            name="dup-test",
            objective_description="desc",
            objective_domain="domain",
            directories=[
                {"name": "src/domain", "purpose": "A"},
                {"name": "src/domain", "purpose": "B"},
            ],
        )
        violations = spec.validate_structure()
        assert any("Duplicate" in v for v in violations)


# ===========================================================================
# Controlled mutation — _apply_approved_change
# ===========================================================================

class TestApprovedChange:
    def setup_method(self):
        self.spec = _minimal_spec()

    def test_bump_version_produces_new_instance(self):
        new_spec = self.spec._apply_approved_change(new_version="0.2.0")
        assert new_spec.meta.version == "0.2.0"
        # original must be unchanged
        assert self.spec.meta.version == "0.1.0"

    def test_add_forbidden_pattern(self):
        new_spec = self.spec._apply_approved_change(add_forbidden=["sqlalchemy"])
        assert "sqlalchemy" in new_spec.constraints.forbidden
        assert "sqlalchemy" not in self.spec.constraints.forbidden

    def test_remove_forbidden_pattern(self):
        new_spec = self.spec._apply_approved_change(remove_forbidden=["django"])
        assert "django" not in new_spec.constraints.forbidden

    def test_add_directory(self):
        new_spec = self.spec._apply_approved_change(
            add_directory={"name": "src/new_module", "purpose": "New module"}
        )
        assert new_spec.has_directory("src/new_module")
        assert not self.spec.has_directory("src/new_module")

    def test_remove_directory(self):
        new_spec = self.spec._apply_approved_change(remove_directory="src/domain")
        assert not new_spec.has_directory("src/domain")

    def test_invalid_version_in_change_raises(self):
        with pytest.raises(ValueError):
            self.spec._apply_approved_change(new_version="not-semver")

    def test_no_change_is_identity(self):
        new_spec = self.spec._apply_approved_change()
        assert new_spec.to_dict() == self.spec.to_dict()


# ===========================================================================
# Serialisation round-trip
# ===========================================================================

class TestSerialisation:
    def test_round_trip_from_dict(self):
        original = _minimal_spec()
        d = original.to_dict()
        reconstructed = ProjectSpec.from_dict(d)
        assert reconstructed.to_dict() == d

    def test_to_dict_contains_all_schema_keys(self):
        d = _minimal_spec().to_dict()
        assert set(d.keys()) == {"meta", "objective", "tech_stack", "constraints", "structure", "ci"}

    def test_from_dict_missing_meta_raises(self):
        with pytest.raises(ValueError, match="meta"):
            ProjectSpec.from_dict({"objective": {"description": "d", "domain": "x"}})

    def test_from_dict_missing_objective_key_raises(self):
        with pytest.raises(ValueError):
            ProjectSpec.from_dict({
                "meta": {"name": "x", "version": "0.1.0"},
                # objective missing entirely
            })

    def test_from_dict_optional_sections_default_to_empty(self):
        """tech_stack, constraints, structure are optional in the YAML."""
        spec = ProjectSpec.from_dict({
            "meta": {"name": "x", "version": "0.1.0"},
            "objective": {"description": "desc", "domain": "dom"},
        })
        assert spec.tech_stack.backend == ()
        assert spec.constraints.forbidden == ()
        assert spec.structure.directories == ()

    def test_list_fields_serialise_as_lists_not_tuples(self):
        """YAML serialisation must use plain lists, not tuple repr."""
        d = _minimal_spec().to_dict()
        assert isinstance(d["tech_stack"]["backend"], list)
        assert isinstance(d["constraints"]["forbidden"], list)
        assert isinstance(d["structure"]["directories"], list)
