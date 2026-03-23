"""
src/infra/project_paths.py — ProjectPaths value object.

Centralises the derivation of all filesystem paths that belong to a project.
It is a pure calculation from (orchestrator_home, project_name) — no I/O,
no state, no config reads.

Responsibility boundary:
  OrchestratorConfig  — knows orchestrator_home and project_name
  ProjectPaths        — knows what lives where inside a project directory
  Factory             — builds ProjectPaths and injects it into adapters

This separation means adapters never need to import OrchestratorConfig.
They receive the exact paths they need as constructor arguments.  Tests can
pass tmp_path without any global state side-effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    """
    All filesystem paths that belong to a single orchestrated project.

    Immutable. Built once per process by the factory and passed by injection.
    Never read from environment variables or config files directly.
    """

    project_home: Path
    tasks_dir: Path
    goals_dir: Path
    registry_path: Path
    repo_url: str           # file:// URL pointing at the local bare repo
    workspace_dir: Path
    logs_dir: Path
    events_dir: Path
    spec_path: Path         # project_spec.yaml canonical location
    project_state_dir: Path # planner's persistent memory (~/<project>/project_state/)
    planner_sessions_dir: Path  # planner session records (~/<project>/planner_sessions/)
    plan_path: Path         # project_plan.yaml canonical location

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_project(cls, orchestrator_home: Path, project_name: str) -> "ProjectPaths":
        """
        Derive all paths for *project_name* under *orchestrator_home*.

        This is the single place where the layout convention is defined:
          <orchestrator_home>/projects/<project_name>/
            tasks/
            goals/
            agents/registry.json
            repo/                   ← local git repo (agents push here)
            workspaces/
            logs/
            events/
            project_spec.yaml
            project_plan.yaml
        """
        base = orchestrator_home / "projects" / project_name
        return cls(
            project_home=base,
            tasks_dir=base / "tasks",
            goals_dir=base / "goals",
            registry_path=base / "agents" / "registry.json",
            repo_url=f"file://{base / 'repo'}",
            workspace_dir=base / "workspaces",
            logs_dir=base / "logs",
            events_dir=base / "events",
            spec_path=base / "project_spec.yaml",
            project_state_dir=base / "project_state",
            planner_sessions_dir=base / "planner_sessions",
            plan_path=base / "project_plan.yaml",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Create all project directories if they do not exist."""
        for d in (
            self.tasks_dir,
            self.goals_dir,
            self.registry_path.parent,
            self.workspace_dir,
            self.logs_dir,
            self.events_dir,
            self.project_state_dir,
            self.planner_sessions_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
