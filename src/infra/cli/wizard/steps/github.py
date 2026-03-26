"""
src/infra/cli/wizard/steps/github.py — Wizard Step 6: GitHub Setup.

Collects the GitHub integration settings needed to run the PR-driven goal
workflow, validates that the target repo is reachable, and writes the CI
workflow template into the managed project's repository.

What this step does:
  1. Prompt for: GitHub token, owner, repo name, base branch.
  2. Validate the token has the required scopes by calling the GitHub API.
  3. Confirm the target repo exists and the token can access it.
  4. Load the project's ProjectSpec to read spec.ci (required_checks, min_approvals).
  5. Render the CI workflow YAML from the spec using render_project_ci().
  6. Write .github/workflows/ci.yml into the managed project's git repo.
  7. Persist the GitHub settings to project.json so the factory can read them.

What it does NOT do:
  - Create a GitHub repo (the team must do this first).
  - Push anything to GitHub — it writes into the local clone of the target repo.
    The operator pushes with their normal git workflow.
  - Modify the orchestrator's own .github/workflows/ci.yml (that's orchestrator CI).

Separating this into its own step means:
  - Teams that use local-only git can skip this step entirely.
  - Teams that add GitHub later can re-run just this step:
      orchestrate init --github-only
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click


# ---------------------------------------------------------------------------
# Public entry point (called by wizard __init__.py)
# ---------------------------------------------------------------------------

def collect_and_setup_github(
    config_data: dict[str, Any],
    *,
    skip_ci_write: bool = False,
) -> bool:
    """
    Run the GitHub setup step interactively.

    Parameters
    ----------
    config_data  : Combined dict from previous wizard steps. Must contain
                   ``project_name`` and ``orchestrator_home``.
    skip_ci_write: If True, validate and persist settings but do not write
                   the CI workflow file (useful for --github-only re-runs
                   where the file already exists and the user said "keep it").

    Returns
    -------
    True  — step succeeded (or was skipped by user choice).
    False — unrecoverable error.
    """
    click.echo(
        "\n  The GitHub integration enables the PR-driven goal workflow:\n"
        "  READY_FOR_REVIEW → (PR opened) → AWAITING_PR_APPROVAL\n"
        "                  → (CI + reviews) → APPROVED → MERGED\n"
        "\n  You can skip this step and configure GitHub later by re-running:\n"
        "    orchestrate init --github-only\n"
    )

    if not click.confirm("  Set up GitHub integration now?", default=True):
        click.echo("  ↳ Skipped. GitHub integration disabled for this project.")
        return True

    # ------------------------------------------------------------------
    # Collect settings
    # ------------------------------------------------------------------
    settings = _collect_github_settings(config_data)
    if settings is None:
        return False

    # ------------------------------------------------------------------
    # Validate token + repo access
    # ------------------------------------------------------------------
    click.echo("\n  Validating GitHub access…")
    ok, error = _validate_github_access(
        token=settings["github_token"],
        owner=settings["github_owner"],
        repo=settings["github_repo"],
    )
    if not ok:
        click.echo(f"\n  ✗ GitHub validation failed: {error}", err=True)
        click.echo(
            "  Check that:\n"
            "    • The token has 'repo' scope (or 'public_repo' for public repos)\n"
            "    • The owner/repo name is correct\n"
            "    • The repo exists on GitHub\n",
            err=True,
        )
        return False
    click.echo("  ✓ GitHub access confirmed.")

    # ------------------------------------------------------------------
    # Persist GitHub settings to project.json
    # ------------------------------------------------------------------
    _persist_github_settings(config_data, settings)
    click.echo("  ✓ GitHub settings saved to project.json.")

    if skip_ci_write:
        return True

    # ------------------------------------------------------------------
    # Load ProjectSpec and render + write the CI workflow
    # ------------------------------------------------------------------
    project_name: str = config_data["project_name"]
    try:
        orchestrator_home = Path(config_data["orchestrator_home"])
    except KeyError:
        from src.infra.config import config as app_config
        orchestrator_home = Path(app_config.orchestrator_home)

    spec = _load_spec(project_name, orchestrator_home)
    if spec is None:
        click.echo(
            "\n  ✗ Could not load project_spec.yaml. "
            "Run Step 4 (Project Specification) first.",
            err=True,
        )
        return False

    if not spec.ci.required_checks:
        click.echo(
            "\n  ⚠  project_spec.yaml has no ci.required_checks defined.\n"
            "  The CI workflow will not be written — there is nothing to gate on.\n"
            "  To add checks later:\n"
            "    orchestrate spec propose  →  add ci.required_checks\n"
            "    orchestrate init --github-only\n"
        )
        return True

    ci_yaml = _render_ci(spec.ci, settings["github_base_branch"], project_name)
    if ci_yaml is None:
        return False

    wrote, dest = _write_ci_workflow(config_data, settings, ci_yaml)
    if not wrote:
        return False

    click.echo(f"\n  ✓ CI workflow written → {dest}")
    click.echo(
        f"  Job names match spec.ci.required_checks: "
        f"{', '.join(spec.ci.required_checks)}"
    )
    click.echo(
        "\n  Next steps:\n"
        "    1. Review and customise the placeholder run steps in:\n"
        f"         {dest}\n"
        "    2. Commit and push to the target repository.\n"
        "    3. Configure branch protection on GitHub:\n"
        f"         Settings → Branches → {settings['github_base_branch']}\n"
        f"         → Require status checks: {', '.join(spec.ci.required_checks)}\n"
        f"         → Require {spec.ci.min_approvals} approving review(s)\n"
    )
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_github_settings(config_data: dict[str, Any]) -> dict[str, Any] | None:
    """Prompt for GitHub settings, reading existing values as defaults."""
    existing = _load_existing_github_settings(config_data)

    click.echo()

    token: str = click.prompt(
        "  GitHub personal access token (needs 'repo' scope)",
        default=existing.get("github_token") or os.environ.get("GITHUB_TOKEN", ""),
        hide_input=True,
    )
    if not token.strip():
        click.echo("  ✗ Token cannot be empty.", err=True)
        return None

    owner: str = click.prompt(
        "  GitHub owner (username or org)",
        default=existing.get("github_owner", ""),
    )
    if not owner.strip():
        click.echo("  ✗ Owner cannot be empty.", err=True)
        return None

    repo: str = click.prompt(
        "  GitHub repository name (without owner prefix)",
        default=existing.get("github_repo", ""),
    )
    if not repo.strip():
        click.echo("  ✗ Repository name cannot be empty.", err=True)
        return None

    base_branch: str = click.prompt(
        "  Base branch (goal PRs target this)",
        default=existing.get("github_base_branch", "main"),
    )

    return {
        "github_token":       token.strip(),
        "github_owner":       owner.strip(),
        "github_repo":        repo.strip(),
        "github_base_branch": base_branch.strip(),
    }


def _validate_github_access(
    token: str, owner: str, repo: str
) -> tuple[bool, str]:
    """
    Call GitHub API to confirm the token can read the repo.
    Returns (True, "") on success or (False, error_message) on failure.
    """
    try:
        from src.infra.github.client import GitHubClient
        client = GitHubClient(token=token, owner=owner, repo=repo, timeout=10)
        # The cheapest way to validate access: list open PRs (max 1 result)
        client.find_open_pr("__nonexistent__", "main")
        return True, ""
    except Exception as exc:
        # Any GitHubError or network error is a validation failure
        return False, str(exc)


def _persist_github_settings(
    config_data: dict[str, Any],
    settings: dict[str, Any],
) -> None:
    """Write GitHub settings into the project's project.json."""
    from src.infra.project_settings import ProjectSettingsManager, ProjectSettings
    try:
        orchestrator_home = Path(config_data["orchestrator_home"])
    except KeyError:
        from src.infra.config import config as app_config
        orchestrator_home = Path(app_config.orchestrator_home)

    project_name: str = config_data["project_name"]
    project_home = orchestrator_home / "projects" / project_name
    manager = ProjectSettingsManager(project_home)
    existing = manager.load()

    updated = ProjectSettings(
        source_repo_url=existing.source_repo_url,
        github_token=settings["github_token"],
        github_owner=settings["github_owner"],
        github_repo=settings["github_repo"],
        github_base_branch=settings["github_base_branch"],
    )
    manager.save(updated)


def _load_spec(project_name: str, orchestrator_home: Path):
    """Load ProjectSpec, returning None on failure."""
    try:
        from src.infra.fs.project_spec_repository import FileProjectSpecRepository
        repo = FileProjectSpecRepository(orchestrator_home=orchestrator_home)
        return repo.load(project_name)
    except Exception:
        return None


def _render_ci(ci, base_branch: str, project_name: str) -> str | None:
    """Render the CI workflow YAML, returning None on error."""
    try:
        from src.infra.templates.project_ci import render_project_ci
        return render_project_ci(ci, base_branch=base_branch, project_name=project_name)
    except ValueError as exc:
        click.echo(f"\n  ✗ Could not render CI workflow: {exc}", err=True)
        return None


def _write_ci_workflow(
    config_data: dict[str, Any],
    settings: dict[str, Any],
    ci_yaml: str,
) -> tuple[bool, Path]:
    """
    Write the rendered CI YAML into the target repo's .github/workflows/ directory.

    The target repo path is derived from the project's repo_url (source_repo_url
    or the local orchestrator repo path). Returns (success, destination_path).
    """
    try:
        orchestrator_home = Path(config_data["orchestrator_home"])
    except KeyError:
        from src.infra.config import config as app_config
        orchestrator_home = Path(app_config.orchestrator_home)

    project_name: str = config_data["project_name"]

    # Derive the target repo path from project settings
    from src.infra.project_settings import ProjectSettingsManager
    project_home = orchestrator_home / "projects" / project_name
    ps = ProjectSettingsManager(project_home).load()

    if ps.source_repo_url and not ps.source_repo_url.startswith("file://"):
        # Remote repo: we can't write directly — guide the operator instead
        click.echo(
            "\n  ⚠  The target repository is remote. Writing CI workflow locally.\n"
            "  You will need to commit and push it to the remote repo.\n"
        )

    # Fall back to the local orchestrator repo path for this project
    from src.infra.project_paths import ProjectPaths
    paths = ProjectPaths.for_project(orchestrator_home, project_name)
    repo_path = Path(str(paths.repo_url).replace("file://", ""))

    dest = repo_path / ".github" / "workflows" / "ci.yml"

    # Check for existing file
    if dest.exists():
        click.echo(f"\n  A CI workflow already exists at:\n    {dest}")
        if not click.confirm("  Overwrite it?", default=False):
            click.echo("  ↳ Keeping existing CI workflow.")
            return True, dest

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(ci_yaml, encoding="utf-8")
        return True, dest
    except OSError as exc:
        click.echo(f"\n  ✗ Failed to write CI workflow: {exc}", err=True)
        return False, dest


def _load_existing_github_settings(config_data: dict[str, Any]) -> dict[str, Any]:
    """Load existing GitHub settings from project.json, return {} on any failure."""
    try:
        orchestrator_home = Path(config_data["orchestrator_home"])
        project_name = config_data["project_name"]
        from src.infra.project_settings import ProjectSettingsManager
        project_home = orchestrator_home / "projects" / project_name
        settings = ProjectSettingsManager(project_home).load()
        return {
            "github_token":       getattr(settings, "github_token", ""),
            "github_owner":       getattr(settings, "github_owner", ""),
            "github_repo":        getattr(settings, "github_repo", ""),
            "github_base_branch": getattr(settings, "github_base_branch", "main"),
        }
    except Exception:
        return {}
