from __future__ import annotations

from pydantic import BaseModel

from domain.entities.task import Task
from domain.errors.tasks_errors import InvalidTransitionError
from domain.value_objects.tasks_vos import Status, TERMINAL


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

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL
