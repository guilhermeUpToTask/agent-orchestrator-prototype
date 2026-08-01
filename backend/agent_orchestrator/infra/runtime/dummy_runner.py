"""
src/infra/runtime/dummy_runner.py — the agent_runner.mode=dry-run runtime.

DryRunAgentRunner adds deterministic workspace artifacts to the scriptable
DummyAgentRunner. It implements the same AgentRunner port and raises TaskFailed
with the same shared FailureKind taxonomy as real CLI runners, so dry-run flows
exercise both verification and retry/terminal paths.

The implementation lives in src/app/testing/fakes.py (the application layer may
not import infra, so the sharing points this way); this module is the infra
name the container wires.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_orchestrator.app.testing.fakes import DummyAgentRunner, DummyBehavior
from agent_orchestrator.app.ports import AgentEventSink, WorkspaceHandle
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.value_objects.tasks_vos import TaskResult



def safe_task_id(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", task_id).strip("_") or "task"


def dry_run_check_path(task_id: str) -> str:
    """Where the dry-run author writes its stand-in check."""
    return f"tests/test_dry_run_{safe_task_id(task_id)}.txt"


def dry_run_candidate_path(task_id: str) -> str:
    """Where the dry-run implementer writes its stand-in candidate.

    `StubReasoner` names this path in its verification command so Tier 0 has a
    check that is genuinely RED before the implementer runs and GREEN after —
    the stub used `git diff --check`, which cannot fail on a clean worktree, so
    every green Tier 0 run proved the lifecycle and nothing about verification.
    """
    return f".orchestrator/dry-run/{safe_task_id(task_id)}.txt"


class DryRunAgentRunner(DummyAgentRunner):
    """Exercise the cyclic execution pipeline with deterministic local artifacts.

    A dry run still traverses Git isolation, test freezing, candidate scope
    validation, executable verification, and evidence publication. The artifacts
    stand in for edits that a real coding runtime would make.
    """

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        return safe_task_id(task_id)

    async def run(
        self,
        task: Task,
        spec: AgentSpec,
        *,
        idempotency_key: str,
        event_sink: AgentEventSink,
        workspace: WorkspaceHandle,
    ) -> TaskResult:
        root = Path(workspace.path)
        if task.tdd_stage == "test_authoring":
            artifact = root / dry_run_check_path(task.id)
            content = f"dry-run executable check for {task.id}\n"
        else:
            artifact = root / dry_run_candidate_path(task.id)
            content = f"dry-run candidate for {task.id} revision {task.revision}\n"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(content)
        return await super().run(
            task,
            spec,
            idempotency_key=idempotency_key,
            event_sink=event_sink,
            workspace=workspace,
        )


__all__ = ["DryRunAgentRunner", "DummyAgentRunner", "DummyBehavior"]
