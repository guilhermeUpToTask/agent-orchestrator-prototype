from __future__ import annotations

from pydantic import BaseModel

from src.domain.entities.task import Task
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.value_objects.lifecycle import Status, TERMINAL


class Goal(BaseModel):
    """Phase-level chunk owning an ordered task list. Guarded self-transitions,
    called only by the Plan. `depends_on` is the DAG seam (unused in a chain)."""

    id: str
    name: str
    position: int
    description: str
    status: Status = Status.PENDING
    tasks: list[Task] = []
    depends_on: list[str] = []

    def _guard(self, allowed_from: set[Status], to: Status) -> None:
        if self.status not in allowed_from:
            raise InvalidTransitionError("Goal", self.id, self.status.value, to.value)

    def start(self) -> None:
        self._guard({Status.PENDING, Status.RUNNING}, Status.RUNNING)
        self.status = Status.RUNNING

    def complete(self) -> None:
        self._guard({Status.RUNNING, Status.PENDING}, Status.DONE)
        self.status = Status.DONE

    def fail(self) -> None:
        self._guard({Status.RUNNING, Status.PENDING}, Status.FAILED)
        self.status = Status.FAILED

    def skip(self) -> None:
        """Close the goal as SKIPPED (its iteration was abandoned by a replan).
        Allowed from PENDING always; from RUNNING only once every task is terminal
        — the finalize-abandon path closes the tasks first, then the goal. A
        RUNNING goal with live tasks can never be skipped out from under them."""
        if self.status == Status.RUNNING and not all(t.is_terminal for t in self.tasks):
            raise InvalidTransitionError(
                "Goal", self.id, self.status.value, Status.SKIPPED.value
            )
        self._guard({Status.PENDING, Status.RUNNING}, Status.SKIPPED)
        self.status = Status.SKIPPED

    def reopen(self) -> None:
        """Re-enter a finished goal because one of its tasks was reopened (human
        redo). DONE -> RUNNING so the scan re-selects the goal."""
        self._guard({Status.DONE}, Status.RUNNING)
        self.status = Status.RUNNING

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL
