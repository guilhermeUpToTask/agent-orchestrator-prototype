from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.domain.value_objects.lifecycle import FailureKind


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
    failure_kind: FailureKind | None = None
    metadata: dict[str, str] = {}

    @classmethod
    def success(
        cls,
        output: str,
        artifacts: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> "TaskResult":
        return cls(
            status="success",
            output=output,
            artifacts=artifacts or {},
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls, reason: str, kind: FailureKind | None = None, output: str = ""
    ) -> "TaskResult":
        return cls(
            status="failure", output=output, failure_reason=reason, failure_kind=kind
        )
