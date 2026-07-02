from __future__ import annotations

from enum import Enum
from typing import Literal


from pydantic import BaseModel


class Status(str, Enum):
    """Lifecycle state shared by goals and tasks. str-based so comparisons and JSON
    serialization are natural (task.status == Status.DONE works)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


# States the navigation scan treats as finished. A FAILED node being terminal is
# what prevents the infinite-loop: it is skipped, never re-selected forever.
TERMINAL: frozenset[Status] = frozenset({Status.DONE, Status.SKIPPED, Status.FAILED})


class TaskResult(BaseModel):
    """Typed output of a task run, and the idempotency record: if set, the work
    already happened and must not re-execute.

    Design: `status` and `output` are ALWAYS present (always assertable in tests);
    `artifacts` is the flexible per-task-type payload (a code task stores
    files_changed, a research task stores sources) so we don't force one rigid
    schema across all task types. This structure is the seam that makes
    orchestration deterministically testable: tests fabricate TaskResults by hand,
    production builds them from the agent — the orchestration treats both identically.
    """

    status: Literal["success", "failure"]
    output: str
    artifacts: dict[str, str] = {}
    failure_reason: str | None = None
    metadata: dict[str, str] = {}

    @classmethod
    def success(cls, output: str, **kw: object) -> "TaskResult":
        return cls(status="success", output=output, **kw)  # type: ignore[arg-type]

    @classmethod
    def failure(cls, reason: str, output: str = "") -> "TaskResult":
        return cls(status="failure", output=output, failure_reason=reason)
