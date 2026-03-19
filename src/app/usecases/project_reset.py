"""
src/app/usecases/project_reset.py — Full project reset use case.

Destructive operator action: wipe all tasks, leases, branches, and
optionally the agent registry. Intended for development resets and
clean-slate restarts.

Steps executed in order:
  1. Delete all task records from the repository
  2. Revoke active Redis leases for every deleted task
  3. Delete remote Git branches matching task-<id> naming
  4. Clear the agent registry (unless keep_agents=True)

Each step is attempted independently — a failure in one step does not
abort the rest. Results are collected in ProjectResetResult.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

from src.domain.ports import LeasePort
from src.domain.repositories import AgentRegistryPort, TaskRepositoryPort


@dataclass
class ProjectResetResult:
    tasks_deleted: int = 0
    leases_released: int = 0
    branches_deleted: int = 0
    agents_removed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def had_errors(self) -> bool:
        return bool(self.errors)


class ProjectResetUseCase:
    """
    Wipe all project state in one destructive operation.

    keep_agents=True skips step 4 so the agent registry survives the reset.
    repo_url is optional — if absent, branch deletion is skipped.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        agent_registry: AgentRegistryPort,
        repo_url: Optional[str] = None,
    ) -> None:
        self._repo     = task_repo
        self._lease    = lease_port
        self._registry = agent_registry
        self._repo_url = repo_url

    def execute(self, keep_agents: bool = False) -> ProjectResetResult:
        result = ProjectResetResult()

        # 1. Delete all task records
        task_ids: list[str] = []
        try:
            tasks = self._repo.list_all()
            task_ids = [t.task_id for t in tasks]
            for tid in task_ids:
                self._repo.delete(tid)
            result.tasks_deleted = len(task_ids)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"tasks: {exc}")

        # 2. Revoke active leases for every deleted task
        for tid in task_ids:
            try:
                if self._lease.is_lease_active(tid):
                    agent_id = self._lease.get_lease_agent(tid)
                    if agent_id:
                        self._lease.revoke_lease(f"{tid}:{agent_id}")
                        result.leases_released += 1
            except Exception:  # noqa: BLE001
                pass

        # 3. Delete remote Git branches
        if self._repo_url:
            result.branches_deleted = self._delete_branches(task_ids)

        # 4. Clear agent registry
        if not keep_agents:
            try:
                agents = self._registry.list_agents()
                for agent in agents:
                    self._registry.deregister(agent.agent_id)
                result.agents_removed = len(agents)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"registry: {exc}")

        return result

    def _delete_branches(self, task_ids: list[str]) -> int:
        deleted = 0
        for tid in task_ids:
            branch = f"task-{tid}"
            try:
                proc = subprocess.run(
                    ["git", "push", self._repo_url, f":{branch}"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode == 0:
                    deleted += 1
            except Exception:  # noqa: BLE001
                pass
        return deleted
