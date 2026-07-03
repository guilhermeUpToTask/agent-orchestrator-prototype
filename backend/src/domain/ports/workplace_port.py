"""The workspace port: where a task attempt's file changes live.

The git adapter makes begin/commit/discard real branch operations
(task/<task_id>/a<attempt> worktree off plan/<plan_id>; commit = --no-ff
merge, discard = worktree + branch deleted — the rollback).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceHandle(Protocol):
    @property
    def path(self) -> str: ...


@runtime_checkable
class Workspace(Protocol):
    """Git-branching seam. NoOp now (handle.path = shared dir); git adapter later
    makes begin/commit/discard real branch operations."""

    async def begin(
        self, plan_id: str, task_id: str, attempt: int
    ) -> WorkspaceHandle: ...
    async def commit(self, handle: WorkspaceHandle) -> None: ...
    async def discard(self, handle: WorkspaceHandle) -> None: ...
