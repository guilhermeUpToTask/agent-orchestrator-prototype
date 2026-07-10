from __future__ import annotations

from pydantic import BaseModel
from datetime import datetime

from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.value_objects.lifecycle import FailureKind, Status, TERMINAL
from src.domain.value_objects.tasks_vos import TaskResult


class Task(BaseModel):
    id: str
    name: str
    position: int
    description: str
    # Capability *ids* (references into the catalog), not embedded Capability entities.
    required_capabilities: list[str] = []
    agent_id: str | None = None
    status: Status = Status.PENDING
    result: TaskResult | None = None
    attempt: int = 0
    # Human-requested redos (reopen) tracked separately from failure attempts so
    # backoff/retry math stays meaningful.
    reopen_count: int = 0
    retry_not_before: datetime | None = None

    def _guard(self, allowed_from: set[Status], to: Status) -> None:
        if self.status not in allowed_from:
            raise InvalidTransitionError("Task", self.id, self.status.value, to.value)

    def start(self) -> None:
        # may start from PENDING(fresh) or RUNNING (idempotent re-pick after crash)
        self._guard({Status.PENDING, Status.RUNNING}, Status.RUNNING)
        self.status = Status.RUNNING
        self.attempt += 1
        self.retry_not_before = None

    def complete(self, result: TaskResult) -> None:
        self._guard({Status.RUNNING}, Status.DONE)
        self.result = result
        self.status = Status.DONE

    def fail(self, reason: str, kind: FailureKind | None = None) -> None:
        self._guard({Status.RUNNING, Status.PENDING}, Status.FAILED)
        self.status = Status.FAILED
        if self.result is None:
            self.result = TaskResult.failure(reason, kind)
        else:
            self.result.failure_reason = reason
            self.result.failure_kind = kind

    def requeue(self, not_before: datetime | None = None) -> None:
        """Return to PENDING for retry. Result cleared; attempts preserved.
        `not_before` sets the backoff gate — the task won't be selected by the
        scan until then (durable, survives crashes)."""
        self._guard({Status.RUNNING, Status.FAILED}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.retry_not_before = not_before

    def skip(self) -> None:
        """Mark the task terminal-SKIPPED without running it (work became
        unnecessary). SKIPPED is terminal so the scan passes over it, and unlike
        FAILED it does not trip the goal-failure signal."""
        self._guard({Status.PENDING}, Status.SKIPPED)
        self.status = Status.SKIPPED

    def abandon(self) -> None:
        """Terminal-skip an in-flight task whose iteration was abandoned by a
        replan (tolerant finalize). Distinct from skip() so the normal skip guard
        stays strict: only the abandon paths may close a RUNNING task."""
        self._guard({Status.RUNNING, Status.PENDING}, Status.SKIPPED)
        self.status = Status.SKIPPED

    def retry(self) -> None:
        """Human-driven retry of a FAILED task (resume-from-pause, decision #17).
        Back to PENDING with a FRESH attempt budget — attempt resets so the retry
        policy cannot immediately re-exhaust — bypassing should_retry by
        construction. Clears the failed result (the TaskFailedEvent and the
        agent_events rows remain the record of the failure)."""
        self._guard({Status.FAILED}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.attempt = 0
        self.retry_not_before = None

    def clear_backoff(self) -> None:
        """Human resume: drop an armed backoff gate so the scan re-selects the
        task immediately. Not a transition — status is untouched."""
        self.retry_not_before = None

    def reopen(self) -> None:
        """Human-driven redo of a good result (DONE -> PENDING). Clears the result
        so the scan re-selects the task; counted on reopen_count, NOT attempt, so
        a redo never eats into the failure/retry budget."""
        self._guard({Status.DONE}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.reopen_count += 1
        self.retry_not_before = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def is_ready_at(self, now: datetime) -> bool:
        """True if the task is eligible to run at `now` — i.e. not gated by an
        unexpired backoff. A method (not a @property) because `now` must be
        injected: the domain never reads the clock itself."""
        return self.retry_not_before is None or self.retry_not_before <= now
