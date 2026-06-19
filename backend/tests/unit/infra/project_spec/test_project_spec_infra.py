"""
tests/unit/infra/project_spec/test_project_spec_infra.py

Unit tests for the infrastructure persistence layer:

  - FileProjectSpecRepository.load() / .save() / .exists()
  - Atomic write guarantee (temp file → rename)
  - YAML serialisation fidelity
  - Error translation (SpecNotFoundError, SpecValidationError)
  - Round-trip: save → load → compare
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.project_spec.errors import SpecNotFoundError, SpecValidationError
from src.infra.fs.project_spec_repository import FileProjectSpecRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    name: str = "my-project",
    version: str = "0.1.0",
    forbidden: list[str] | None = None,
    required: list[str] | None = None,
) -> ProjectSpec:
    return ProjectSpec.create(
        name=name,
        objective_description="Test project for infra tests",
        objective_domain="testing",
        backend=["python"],
        database=["redis"],
        infra=["docker"],
        forbidden=forbidden or ["django"],
        required=required or ["pydantic"],
        directories=[{"name": "src/domain", "purpose": "Domain layer"}],
        version=version,
    )


def _make_repo(tmp_path: Path) -> FileProjectSpecRepository:
    return FileProjectSpecRepository(orchestrator_home=tmp_path)


def _expected_path(tmp_path: Path, project_name: str) -> Path:
    return tmp_path / "projects" / project_name / "project_spec.yaml"


# ===========================================================================
# exists()
# ===========================================================================

class TestExists:
    def test_returns_false_when_file_missing(self, tmp_path):
        repo = _make_repo(tmp_path)
        assert repo.exists("no-such-project") is False

    def test_returns_true_after_save(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec()
        repo.save(spec)
        assert repo.exists(spec.name) is True


# ===========================================================================
# save()
# ===========================================================================

class TestSave:
    def test_creates_file_at_expected_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec(name="my-project")
        repo.save(spec)
        assert _expected_path(tmp_path, "my-project").exists()

    def test_creates_intermediate_directories(self, tmp_path):
        nested_home = tmp_path / "deep" / "nested"
        repo = FileProjectSpecRepository(orchestrator_home=nested_home)
        spec = _make_spec()
        repo.save(spec)
        assert (nested_home / "projects" / spec.name / "project_spec.yaml").exists()

    def test_written_content_is_valid_yaml(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec()
        repo.save(spec)
        raw = _expected_path(tmp_path, spec.name).read_text()
        parsed = yaml.safe_load(raw)
        assert isinstance(parsed, dict)
        assert parsed["meta"]["name"] == spec.name

    def test_overwrite_replaces_previous_version(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec_v1 = _make_spec(version="0.1.0")
        spec_v2 = _make_spec(version="0.2.0")
        repo.save(spec_v1)
        repo.save(spec_v2)
        loaded = repo.load("my-project")
        assert loaded.meta.version == "0.2.0"

    def test_no_leftover_tmp_files_after_save(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec()
        repo.save(spec)
        project_dir = _expected_path(tmp_path, spec.name).parent
        tmp_files = list(project_dir.glob("*.tmp_*"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"


# ===========================================================================
# load()
# ===========================================================================

class TestLoad:
    def test_round_trip_is_lossless(self, tmp_path):
        repo = _make_repo(tmp_path)
        original = _make_spec(
            name="roundtrip-project",
            forbidden=["django", "flask", "celery"],
            required=["pydantic", "redis"],
        )
        repo.save(original)
        loaded = repo.load("roundtrip-project")
        assert loaded.to_dict() == original.to_dict()

    def test_raises_spec_not_found_for_missing_project(self, tmp_path):
        repo = _make_repo(tmp_path)
        with pytest.raises(SpecNotFoundError) as exc_info:
            repo.load("ghost-project")
        assert "ghost-project" in str(exc_info.value)

    def test_raises_spec_validation_error_for_corrupt_yaml(self, tmp_path):
        repo = _make_repo(tmp_path)
        path = _expected_path(tmp_path, "broken")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(":::not valid yaml:::", encoding="utf-8")
        with pytest.raises(SpecValidationError):
            repo.load("broken")

    def test_raises_spec_validation_error_for_non_mapping_yaml(self, tmp_path):
        repo = _make_repo(tmp_path)
        path = _expected_path(tmp_path, "list-project")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(SpecValidationError, match="mapping"):
            repo.load("list-project")

    def test_raises_spec_validation_error_for_missing_required_key(self, tmp_path):
        repo = _make_repo(tmp_path)
        path = _expected_path(tmp_path, "incomplete")
        path.parent.mkdir(parents=True, exist_ok=True)
        # missing "objective"
        path.write_text(
            "meta:\n  name: incomplete\n  version: '0.1.0'\n",
            encoding="utf-8",
        )
        with pytest.raises(SpecValidationError):
            repo.load("incomplete")

    def test_loads_optional_sections_when_absent(self, tmp_path):
        """tech_stack, constraints, structure are optional in YAML."""
        repo = _make_repo(tmp_path)
        path = _expected_path(tmp_path, "minimal")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent("""\
                meta:
                  name: minimal
                  version: '0.1.0'
                objective:
                  description: Minimal spec
                  domain: testing
            """),
            encoding="utf-8",
        )
        spec = repo.load("minimal")
        assert spec.tech_stack.backend == ()
        assert spec.constraints.forbidden == ()

    def test_constraint_lists_preserved(self, tmp_path):
        repo = _make_repo(tmp_path)
        original = _make_spec(forbidden=["django", "flask"], required=["pydantic", "redis"])
        repo.save(original)
        loaded = repo.load(original.name)
        assert set(loaded.constraints.forbidden) == {"django", "flask"}
        assert set(loaded.constraints.required) == {"pydantic", "redis"}

    def test_directory_rules_preserved(self, tmp_path):
        repo = _make_repo(tmp_path)
        original = ProjectSpec.create(
            name="dir-test",
            objective_description="desc",
            objective_domain="dom",
            directories=[
                {"name": "src/domain", "purpose": "Domain layer"},
                {"name": "src/infra", "purpose": "Infrastructure"},
            ],
        )
        repo.save(original)
        loaded = repo.load("dir-test")
        names = {d.name for d in loaded.structure.directories}
        assert names == {"src/domain", "src/infra"}


# ===========================================================================
# Canonical YAML schema shape
# ===========================================================================

class TestYamlSchema:
    def test_yaml_contains_all_top_level_keys(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec()
        repo.save(spec)
        raw = yaml.safe_load(_expected_path(tmp_path, spec.name).read_text())
        assert set(raw.keys()) >= {"meta", "objective", "tech_stack", "constraints", "structure"}

    def test_yaml_uses_block_style_not_flow(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec()
        repo.save(spec)
        text = _expected_path(tmp_path, spec.name).read_text()
        # block style uses "- " for list items; flow style uses "[...]"
        assert "- " in text or text.count(":") > 3  # sanity check
        assert "[" not in text  # no flow-style lists

    def test_meta_name_matches_spec_name(self, tmp_path):
        repo = _make_repo(tmp_path)
        spec = _make_spec(name="acme-project")
        repo.save(spec)
        raw = yaml.safe_load(_expected_path(tmp_path, "acme-project").read_text())
        assert raw["meta"]["name"] == "acme-project"
