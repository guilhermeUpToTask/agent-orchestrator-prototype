"""
tests/unit/app/usecases/test_project_spec_usecases.py

Unit tests for the three ProjectSpec use cases:

  - LoadProjectSpec  — delegates to repo, propagates domain errors
  - ValidateAgainstSpec — constraint checking against live spec
  - ProposeSpecChange — staged proposal writes, never touches live spec
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.project_spec.errors import SpecNotFoundError, SpecValidationError
from src.app.usecases.load_project_spec import LoadProjectSpec
from src.app.usecases.validate_against_spec import ValidateAgainstSpec, ValidationResult
from src.app.usecases.propose_spec_change import (
    ChangeProposal,
    ProposeSpecChange,
)
from src.infra.fs.project_spec_repository import FileProjectSpecRepository


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_spec(
    name: str = "my-project",
    forbidden: list[str] | None = None,
    required: list[str] | None = None,
) -> ProjectSpec:
    return ProjectSpec.create(
        name=name,
        objective_description="A project for testing use cases",
        objective_domain="testing",
        backend=["python"],
        forbidden=forbidden or ["django", "flask"],
        required=required or ["pydantic"],
        directories=[
            {"name": "src/domain", "purpose": "Domain layer"},
            {"name": "src/infra", "purpose": "Infra layer"},
        ],
    )


# ===========================================================================
# LoadProjectSpec
# ===========================================================================

class TestLoadProjectSpec:
    def test_returns_spec_from_repo(self):
        spec = _make_spec()
        repo = MagicMock()
        repo.load.return_value = spec
        uc = LoadProjectSpec(repo)
        result = uc.execute("my-project")
        assert result is spec
        repo.load.assert_called_once_with("my-project")

    def test_propagates_spec_not_found(self):
        repo = MagicMock()
        repo.load.side_effect = SpecNotFoundError("ghost")
        uc = LoadProjectSpec(repo)
        with pytest.raises(SpecNotFoundError):
            uc.execute("ghost")

    def test_propagates_spec_validation_error(self):
        repo = MagicMock()
        repo.load.side_effect = SpecValidationError("bad-project", "bad yaml")
        uc = LoadProjectSpec(repo)
        with pytest.raises(SpecValidationError):
            uc.execute("bad-project")


# ===========================================================================
# ValidateAgainstSpec
# ===========================================================================

class TestValidateAgainstSpec:

    def setup_method(self):
        self.spec = _make_spec(forbidden=["django", "flask", "celery"])

    def _uc(self) -> ValidateAgainstSpec:
        return ValidateAgainstSpec(self.spec)

    # --- happy paths --------------------------------------------------------

    def test_clean_task_passes(self):
        result = self._uc().execute(
            task_description="Implement the FastAPI endpoint",
            dependencies=["fastapi", "pydantic"],
            directories=["src/infra"],
        )
        assert result.passed is True
        assert result.violations == []

    def test_returns_validation_result_type(self):
        result = self._uc().execute()
        assert isinstance(result, ValidationResult)

    # --- description checks -------------------------------------------------

    def test_forbidden_in_description_produces_violation(self):
        result = self._uc().execute(
            task_description="Use django ORM to persist data"
        )
        assert result.passed is False
        assert any("django" in v.lower() for v in result.violations)

    def test_case_insensitive_description_check(self):
        result = self._uc().execute(task_description="Use Flask for the API layer")
        assert result.passed is False

    # --- dependency checks --------------------------------------------------

    def test_forbidden_dependency_fails(self):
        result = self._uc().execute(dependencies=["django-rest-framework"])
        assert result.passed is False
        assert any("forbidden" in v.lower() for v in result.violations)

    def test_multiple_forbidden_deps_all_reported(self):
        result = self._uc().execute(dependencies=["django", "flask"])
        violation_text = " ".join(result.violations).lower()
        assert "django" in violation_text
        assert "flask" in violation_text

    def test_allowed_dependency_passes(self):
        result = self._uc().execute(dependencies=["pydantic", "structlog"])
        assert result.passed is True

    # --- directory checks ---------------------------------------------------

    def test_forbidden_directory_path_fails(self):
        spec = _make_spec(forbidden=["legacy"])
        uc = ValidateAgainstSpec(spec)
        result = uc.execute(directories=["src/legacy/handlers"])
        assert result.passed is False

    def test_undeclared_directory_produces_warning_not_violation(self):
        result = self._uc().execute(directories=["src/new_module"])
        assert result.passed is True
        assert any("not declared" in w for w in result.warnings)

    def test_declared_directory_produces_no_warning(self):
        result = self._uc().execute(directories=["src/domain"])
        assert result.passed is True
        assert result.warnings == []

    # --- combined checks ----------------------------------------------------

    def test_multiple_violations_accumulated(self):
        result = self._uc().execute(
            task_description="use django and flask",
            dependencies=["celery"],
        )
        assert len(result.violations) >= 3

    def test_str_representation_shows_failed(self):
        result = self._uc().execute(dependencies=["django"])
        assert "FAILED" in str(result)

    def test_str_representation_shows_passed(self):
        result = self._uc().execute()
        assert "PASSED" in str(result)


# ===========================================================================
# ProposeSpecChange
# ===========================================================================

class TestProposeSpecChange:

    def _make_repo(self, tmp_path: Path) -> FileProjectSpecRepository:
        repo = FileProjectSpecRepository(orchestrator_home=tmp_path)
        spec = _make_spec()
        repo.save(spec)
        return repo

    def _make_uc(self, tmp_path: Path) -> ProposeSpecChange:
        repo = self._make_repo(tmp_path)
        return ProposeSpecChange(spec_repo=repo)

    # --- accepted proposals -------------------------------------------------

    def test_accepted_proposal_returns_true(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute(
            "my-project",
            ChangeProposal(new_version="0.2.0", rationale="Bump version"),
        )
        assert result.accepted is True

    def test_accepted_proposal_has_proposed_spec(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="0.2.0"))
        assert result.proposed_spec is not None
        assert result.proposed_spec.meta.version == "0.2.0"

    def test_proposal_writes_proposed_yaml_not_live(self, tmp_path):
        """The live spec must remain unchanged after a proposal."""
        uc = self._make_uc(tmp_path)
        uc.execute("my-project", ChangeProposal(new_version="99.0.0"))
        # live spec still at original version
        repo = FileProjectSpecRepository(orchestrator_home=tmp_path)
        live = repo.load("my-project")
        assert live.meta.version == "0.1.0"

    def test_proposal_file_exists_at_expected_path(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="0.2.0"))
        assert result.proposal_path is not None
        assert Path(result.proposal_path).exists()
        assert "proposed" in result.proposal_path

    def test_proposal_file_contains_pending_header(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="0.2.0"))
        content = Path(result.proposal_path).read_text()
        assert "PENDING PROPOSAL" in content

    def test_rationale_stored_in_proposal_file(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute(
            "my-project",
            ChangeProposal(
                new_version="0.2.0",
                rationale="Upgrading because of new dependency requirements",
            ),
        )
        content = Path(result.proposal_path).read_text()
        assert "Upgrading because" in content

    def test_add_forbidden_pattern(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute(
            "my-project",
            ChangeProposal(add_forbidden=["sqlalchemy"]),
        )
        assert result.accepted is True
        assert "sqlalchemy" in result.proposed_spec.constraints.forbidden

    def test_remove_forbidden_pattern(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute(
            "my-project",
            ChangeProposal(remove_forbidden=["django"]),
        )
        assert result.accepted is True
        assert "django" not in result.proposed_spec.constraints.forbidden

    def test_add_directory_rule(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute(
            "my-project",
            ChangeProposal(
                add_directory={"name": "src/analytics", "purpose": "Analytics module"}
            ),
        )
        assert result.accepted is True
        assert result.proposed_spec.has_directory("src/analytics")

    # --- rejected proposals -------------------------------------------------

    def test_invalid_version_string_is_rejected(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="bad"))
        assert result.accepted is False
        assert result.rejection_reason is not None

    def test_missing_project_is_rejected(self, tmp_path):
        repo = FileProjectSpecRepository(orchestrator_home=tmp_path)
        uc = ProposeSpecChange(spec_repo=repo)
        result = uc.execute("nonexistent", ChangeProposal(new_version="0.2.0"))
        assert result.accepted is False
        assert result.rejection_reason is not None

    def test_str_representation_of_accepted(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="0.2.0"))
        assert "ACCEPTED" in str(result)

    def test_str_representation_of_rejected(self, tmp_path):
        uc = self._make_uc(tmp_path)
        result = uc.execute("my-project", ChangeProposal(new_version="INVALID"))
        assert "REJECTED" in str(result)
