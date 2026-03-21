"""
tests/unit/infra/test_project_ci_template.py

Unit tests for src/infra/templates/project_ci.py

Verifies:
  - Job names exactly match required_checks (the naming contract)
  - The workflow triggers on goal/** pushes and PRs to base_branch
  - The banner contains the check names and approval count from CIConfig
  - Empty required_checks raises ValueError (no jobs = invalid workflow)
  - The rendered YAML is valid (parseable by yaml.safe_load)
  - Check name slugification works (special chars → valid job ids)
  - Orchestrator CI and project CI are separate files with distinct purposes
"""
from __future__ import annotations

import yaml
import pytest

from src.domain.project_spec.value_objects import CIConfig
from src.infra.templates.project_ci import render_project_ci, _to_job_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ci(*checks: str, min_approvals: int = 1) -> CIConfig:
    return CIConfig(required_checks=list(checks), min_approvals=min_approvals)


def _parse(yaml_text: str) -> dict:
    """Strip the banner comment lines and parse the YAML body."""
    lines = yaml_text.splitlines()
    body_lines = [l for l in lines if not l.startswith("#")]
    return yaml.safe_load("\n".join(body_lines)) or {}


# ---------------------------------------------------------------------------
# 1. Naming contract — job names must match required_checks exactly
# ---------------------------------------------------------------------------

class TestNamingContract:
    def test_single_check_job_name_matches(self):
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="proj")
        workflow = _parse(text)
        job_names = [j["name"] for j in workflow["jobs"].values()]
        assert "tests" in job_names

    def test_multiple_checks_all_present(self):
        text = render_project_ci(
            _ci("tests", "lint", "build"),
            base_branch="main",
            project_name="proj",
        )
        workflow = _parse(text)
        job_names = {j["name"] for j in workflow["jobs"].values()}
        assert job_names == {"tests", "lint", "build"}

    def test_check_names_preserved_exactly(self):
        """Names with hyphens or numbers must come through unchanged."""
        text = render_project_ci(
            _ci("unit-tests", "type-check", "e2e"),
            base_branch="main",
            project_name="proj",
        )
        workflow = _parse(text)
        job_names = {j["name"] for j in workflow["jobs"].values()}
        assert job_names == {"unit-tests", "type-check", "e2e"}

    def test_job_id_derived_from_check_name(self):
        text = render_project_ci(_ci("my-check"), base_branch="main", project_name="proj")
        workflow = _parse(text)
        assert "my-check" in workflow["jobs"]  # hyphens are valid in job ids

    def test_special_chars_in_check_name_produce_valid_job_id(self):
        text = render_project_ci(
            _ci("my check!"),  # space + exclamation are invalid in job ids
            base_branch="main",
            project_name="proj",
        )
        workflow = _parse(text)
        # job id should be sanitised; name should be raw
        job_ids = list(workflow["jobs"].keys())
        assert len(job_ids) == 1
        assert " " not in job_ids[0]
        assert "!" not in job_ids[0]
        assert workflow["jobs"][job_ids[0]]["name"] == "my check!"


# ---------------------------------------------------------------------------
# 2. Workflow triggers
# ---------------------------------------------------------------------------

class TestTriggers:
    def _workflow(self, base: str = "main") -> dict:
        text = render_project_ci(_ci("tests"), base_branch=base, project_name="p")
        return _parse(text)

    def test_triggers_on_goal_push(self):
        w = self._workflow()
        push_branches = w["on"]["push"]["branches"]
        assert "goal/**" in push_branches

    def test_triggers_on_pr_to_base_branch(self):
        w = self._workflow("main")
        pr_branches = w["on"]["pull_request"]["branches"]
        assert "main" in pr_branches

    def test_pr_trigger_uses_custom_base_branch(self):
        w = self._workflow("develop")
        pr_branches = w["on"]["pull_request"]["branches"]
        assert "develop" in pr_branches
        assert "main" not in pr_branches

    def test_no_push_to_base_branch_trigger(self):
        """Pushes directly to main must NOT trigger CI — protected branch."""
        w = self._workflow("main")
        push_branches = w["on"]["push"]["branches"]
        assert "main" not in push_branches


# ---------------------------------------------------------------------------
# 3. Banner content
# ---------------------------------------------------------------------------

class TestBanner:
    def test_banner_contains_check_names(self):
        text = render_project_ci(
            _ci("tests", "lint"),
            base_branch="main",
            project_name="myproj",
        )
        assert "tests" in text
        assert "lint" in text

    def test_banner_contains_min_approvals(self):
        text = render_project_ci(
            _ci("tests", min_approvals=2),
            base_branch="main",
            project_name="myproj",
        )
        assert "2" in text

    def test_banner_contains_project_name(self):
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="awesome-api")
        assert "awesome-api" in text

    def test_banner_explains_naming_contract(self):
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="p")
        assert "required_checks" in text
        assert "project_spec.yaml" in text

    def test_banner_is_comments_only(self):
        """All banner lines must start with # so YAML parsers ignore them."""
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="p")
        banner_lines = [l for l in text.splitlines() if l.startswith("#")]
        assert len(banner_lines) > 5

    def test_banner_mentions_orchestrate_init(self):
        """Users must know how to regenerate the file."""
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="p")
        assert "orchestrate init" in text


# ---------------------------------------------------------------------------
# 4. Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_empty_required_checks_raises(self):
        with pytest.raises(ValueError, match="required_checks"):
            render_project_ci(CIConfig.no_gate(), base_branch="main", project_name="p")

    def test_rendered_yaml_is_parseable(self):
        text = render_project_ci(
            _ci("tests", "lint", "build"),
            base_branch="main",
            project_name="myproj",
        )
        # Must not raise
        parsed = _parse(text)
        assert parsed is not None

    def test_workflow_has_required_top_level_keys(self):
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="p")
        parsed = _parse(text)
        assert "name" in parsed
        assert "on" in parsed
        assert "jobs" in parsed

    def test_each_job_has_steps(self):
        text = render_project_ci(_ci("tests", "lint"), base_branch="main", project_name="p")
        parsed = _parse(text)
        for job in parsed["jobs"].values():
            assert "steps" in job
            assert len(job["steps"]) > 0


# ---------------------------------------------------------------------------
# 5. Job id slugification helper
# ---------------------------------------------------------------------------

class TestToJobId:
    def test_plain_name_unchanged(self):
        assert _to_job_id("tests") == "tests"

    def test_hyphen_preserved(self):
        assert _to_job_id("unit-tests") == "unit-tests"

    def test_space_replaced(self):
        assert " " not in _to_job_id("my tests")

    def test_slash_replaced(self):
        assert "/" not in _to_job_id("ci/tests")

    def test_result_not_empty(self):
        assert _to_job_id("!!!") != ""


# ---------------------------------------------------------------------------
# 6. Separation from orchestrator CI
# ---------------------------------------------------------------------------

class TestCISeparation:
    def test_rendered_workflow_name_does_not_say_orchestrator(self):
        """The project CI workflow must NOT be named 'Orchestrator CI'."""
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="myproj")
        parsed = _parse(text)
        assert "Orchestrator" not in parsed.get("name", "")

    def test_no_reference_to_src_domain(self):
        """Project CI must not import or reference orchestrator source modules."""
        text = render_project_ci(_ci("tests"), base_branch="main", project_name="p")
        assert "src.domain" not in text
        assert "from src" not in text

    def test_orchestrator_ci_file_is_separate(self):
        """The orchestrator's own .github/workflows/ci.yml must exist and test itself."""
        from pathlib import Path
        import yaml as _yaml
        ci_path = Path(__file__).parents[3] / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), "Orchestrator CI file missing"
        text = ci_path.read_text()
        assert "Orchestrator CI" in text
        assert "tests/unit/" in text      # tests the orchestrator
        assert "goal/**" not in text      # does NOT watch goal branches
