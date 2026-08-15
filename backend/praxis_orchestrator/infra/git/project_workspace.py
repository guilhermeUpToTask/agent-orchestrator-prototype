"""Project-scoped workspace routing; no process-global repository fallback."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from praxis_orchestrator.app.ports import UnitOfWork, WorkspaceHandle
from praxis_orchestrator.domain.entities.project_definition import ProjectDefinition
from praxis_orchestrator.domain.repositories.project_repo import ProjectRepository
from praxis_orchestrator.infra.git.repository_binding import GIT_NONINTERACTIVE_ENV
from praxis_orchestrator.infra.git.workspace import GitBranchWorkspace


@dataclass(frozen=True)
class _CachedWorkspace:
    identity: tuple[str | None, str, str]
    workspace: GitBranchWorkspace


class ProjectWorkspaceResolver:
    def __init__(
        self,
        projects: ProjectRepository,
        orchestrator_home: Path,
    ) -> None:
        self._projects = projects
        self._home = orchestrator_home
        self._cache: dict[str, _CachedWorkspace] = {}

    def resolve(self, project_id: str) -> GitBranchWorkspace:
        project = self._projects.get(project_id)
        repo = self.repository_path_for(project)
        self._materialize_remote(project, repo)
        default_branch = self._default_branch(repo)
        identity = (project.repo_url, str(repo), default_branch)
        existing = self._cache.get(project_id)
        if existing is not None and existing.identity == identity:
            return existing.workspace
        workspace = GitBranchWorkspace(
            repo, default_branch=default_branch, allow_init=project.repo_url is None
        )
        self._cache[project_id] = _CachedWorkspace(identity, workspace)
        return workspace

    def workspaces(self) -> list[tuple[str, GitBranchWorkspace]]:
        """Return local workspace adapters without cloning remote repositories."""
        workspaces: list[tuple[str, GitBranchWorkspace]] = []
        for project in self._projects.list():
            repo = self.repository_path_for(project)
            default_branch = self._default_branch(repo)
            identity = (project.repo_url, str(repo), default_branch)
            cached = self._cache.get(project.id)
            if cached is None or cached.identity != identity:
                cached = _CachedWorkspace(
                    identity,
                    GitBranchWorkspace(
                        repo, default_branch=default_branch, allow_init=project.repo_url is None
                    ),
                )
                self._cache[project.id] = cached
            workspaces.append((project.id, cached.workspace))
        return workspaces

    # Names a repository's trunk is conventionally called, best first. Consulted
    # only when there is no remote to ask — see `_default_branch`.
    _CONVENTIONAL_DEFAULTS = ("main", "master", "trunk", "development", "develop")

    @staticmethod
    def _default_branch(repo: Path) -> str:
        """This repository's DEFAULT branch — never merely its current one.

        What this answers is load-bearing: it is the ref new cycle branches are
        cut from, the ref `checkout -B` targets, and the ref the "plan work
        never touches the default branch" guarantee is measured against.

        `symbolic-ref HEAD` used to be the second probe, and it answers a
        different question — "what is checked out right now". A LOCAL project
        has no `origin/HEAD` to answer first, so any checkout in the project
        repository silently redefined its default branch. The demo's own README
        tells an operator to `git switch cycle/<id>` to look at the result, and
        doing so made the next verification report the cycle branch as the
        default and fail a guarantee that was in fact being kept (2026-08-10).

        Order: the remote's declared HEAD (authoritative — a fork whose default
        is `develop` must not be overridden by a local `main`), then a
        conventional trunk name among the local branches, then a lone branch.
        Deliberately no fallback to "whatever is checked out" and none to "the
        first branch alphabetically": a finished cycle leaves `cycle/…`,
        `goal/…` and `task/…` refs, all of which sort before `main`.
        """
        if not (repo / ".git").exists():
            return "main"
        remote_head = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short",
             "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
        )
        if remote_head.returncode == 0 and remote_head.stdout.strip():
            return remote_head.stdout.strip().removeprefix("origin/")
        branches = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        for candidate in ProjectWorkspaceResolver._CONVENTIONAL_DEFAULTS:
            if candidate in branches:
                return candidate
        if len(branches) == 1:
            return branches[0]
        raise ValueError(f"cannot determine default branch for repository {repo}")

    def repository_path_for(self, project: ProjectDefinition) -> Path:
        """Where this project's repository lives (or will live) on disk.

        Public so readiness reads (`routers/readiness.py`, `routers/reference.py`)
        can report the same scratch/local/remote location this resolver actually
        uses, instead of re-deriving the `$PRAXIS_HOME/projects/<id>/repo`
        rule a second time.
        """
        if project.repo_url:
            parsed = urlparse(project.repo_url)
            if parsed.scheme == "file":
                return Path(unquote(parsed.path))
            if parsed.scheme == "":
                return Path(project.repo_url).expanduser().resolve()
            repository_identity = hashlib.sha256(project.repo_url.encode()).hexdigest()[:16]
            return self._home / "projects" / project.id / "repos" / repository_identity
        return self._home / "projects" / project.id / "repo"

    def _repository_path(self, project: ProjectDefinition) -> Path:
        """Private alias kept for existing internal callers/tests written
        against the resolver's original name; the rule itself lives only in
        `repository_path_for`."""
        return self.repository_path_for(project)

    def _materialize_remote(self, project: ProjectDefinition, destination: Path) -> None:
        if not project.repo_url:
            return
        scheme = urlparse(project.repo_url).scheme
        if scheme in ("", "file") or (destination / ".git").exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--", project.repo_url, str(destination)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **GIT_NONINTERACTIVE_ENV},
        )


@dataclass
class RoutedWorkspaceHandle:
    path: str
    base_ref: str | None
    workspace: GitBranchWorkspace
    delegate: WorkspaceHandle


class ProjectRoutingWorkspace:
    """Resolve plan -> immutable project -> repository for every new attempt."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        resolver: ProjectWorkspaceResolver,
    ) -> None:
        self._uow_factory = uow_factory
        self._resolver = resolver

    async def begin(
        self,
        plan_id: str,
        task_id: str,
        attempt: int,
        *,
        cycle_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        base_ref: str | None = None,
    ) -> RoutedWorkspaceHandle:
        uow = self._uow_factory()
        with uow:
            plan = uow.plans.get(plan_id)
        if plan.project_id is None:
            raise ValueError(f"plan {plan_id} has no project binding")
        workspace = self._resolver.resolve(plan.project_id)
        delegate = await workspace.begin(
            plan_id,
            task_id,
            attempt,
            cycle_id=cycle_id,
            goal_id=goal_id,
            run_id=run_id,
            base_ref=base_ref,
        )
        return RoutedWorkspaceHandle(
            path=delegate.path,
            base_ref=delegate.base_ref,
            workspace=workspace,
            delegate=delegate,
        )

    async def snapshot(self, handle: WorkspaceHandle) -> str:
        if not isinstance(handle, RoutedWorkspaceHandle):
            raise TypeError("workspace handle was not project-routed")
        return await handle.workspace.snapshot(handle.delegate)

    async def checkpoint(self, handle: WorkspaceHandle) -> str:
        if not isinstance(handle, RoutedWorkspaceHandle):
            raise TypeError("workspace handle was not project-routed")
        return await handle.workspace.checkpoint(handle.delegate)

    async def merge_goal(self, plan_id: str, cycle_id: str, goal_id: str) -> str:
        uow = self._uow_factory()
        with uow:
            plan = uow.plans.get(plan_id)
        if plan.project_id is None:
            raise ValueError(f"plan {plan_id} has no project binding")
        return await self._resolver.resolve(plan.project_id).merge_goal(plan_id, cycle_id, goal_id)

    async def commit(self, handle: WorkspaceHandle) -> None:
        if not isinstance(handle, RoutedWorkspaceHandle):
            raise TypeError("workspace handle was not project-routed")
        await handle.workspace.commit(handle.delegate)

    async def discard(self, handle: WorkspaceHandle) -> None:
        if not isinstance(handle, RoutedWorkspaceHandle):
            raise TypeError("workspace handle was not project-routed")
        await handle.workspace.discard(handle.delegate)

    async def prune(self) -> None:
        for _project_id, workspace in self._resolver.workspaces():
            await workspace.prune()

    async def audit(self) -> dict[str, dict[str, list[str]]]:
        return {
            project_id: await workspace.audit()
            for project_id, workspace in self._resolver.workspaces()
        }
