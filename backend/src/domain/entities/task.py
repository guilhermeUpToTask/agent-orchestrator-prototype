from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.domain.entities.execution_contracts import (
    TaskContract,
    TestBundle,
    VerificationEvidence,
)
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.value_objects.lifecycle import FailureKind, Status, TERMINAL
from src.domain.value_objects.tasks_vos import TaskResult


class Task(BaseModel):
    id: str
    name: str
    position: int
    description: str
    required_capabilities: list[str] = []
    agent_id: str | None = None
    role_agent_ids: dict[str, str] = {}
    status: Status = Status.PENDING
    result: TaskResult | None = None

    # Absolute execution identity. This value never decreases or resets.
    attempt: int = 0
    # Human retry cycles and the attempt counter consumed by RetryPolicy.
    retry_cycle: int = 0
    cycle_attempt: int = 0
    # Semantic edits invalidate revision-bound tests and late results.
    revision: int = 1
    contract: TaskContract | None = None
    test_bundle: TestBundle | None = None
    verification_evidence: list[VerificationEvidence] = []

    reopen_count: int = 0
    retry_not_before: datetime | None = None

    def _guard(self, allowed_from: set[Status], to: Status) -> None:
        if self.status not in allowed_from:
            raise InvalidTransitionError("Task", self.id, self.status.value, to.value)

    def start(self) -> None:
        self._guard({Status.PENDING, Status.RUNNING}, Status.RUNNING)
        self.status = Status.RUNNING
        self.attempt += 1
        self.cycle_attempt += 1
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
        self._guard({Status.RUNNING, Status.FAILED}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.retry_not_before = not_before

    def skip(self) -> None:
        self._guard({Status.PENDING}, Status.SKIPPED)
        self.status = Status.SKIPPED

    def abandon(self) -> None:
        self._guard({Status.RUNNING, Status.PENDING}, Status.SKIPPED)
        self.status = Status.SKIPPED

    def retry(self) -> None:
        """Start a new human retry cycle without reusing absolute identity."""
        self._guard({Status.FAILED}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.retry_cycle += 1
        self.cycle_attempt = 0
        self.retry_not_before = None

    def semantic_edit(self, *, name: str | None = None, description: str | None = None) -> None:
        """Revise executable meaning and invalidate revision-bound artifacts."""
        self._guard({Status.PENDING, Status.FAILED}, Status.PENDING)
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        self.status = Status.PENDING
        self.result = None
        self.revision += 1
        if self.contract is not None:
            self.contract = self.contract.model_copy(
                update={
                    "revision": self.revision,
                    "objective": self.description or self.name,
                }
            )
        if self.test_bundle is not None:
            self.test_bundle.invalidate("semantic task edit")
        self.verification_evidence = []
        self.cycle_attempt = 0
        self.retry_not_before = None

    def clear_backoff(self) -> None:
        self.retry_not_before = None

    def reopen(self) -> None:
        self._guard({Status.DONE}, Status.PENDING)
        self.status = Status.PENDING
        self.result = None
        self.reopen_count += 1
        self.retry_cycle += 1
        self.cycle_attempt = 0
        self.retry_not_before = None

    @property
    def tdd_stage(self) -> str:
        if self.contract is None:
            return "contract"
        if self.test_bundle is None or not self.test_bundle.validates(self.id, self.revision):
            return "test_authoring"
        if not self.verification_evidence:
            return "implementation"
        if not all(item.accepted for item in self.verification_evidence):
            return "verification"
        return "completed" if self.status == Status.DONE else "merge"

    def freeze_test_bundle(self, bundle: TestBundle) -> None:
        if self.status not in {Status.PENDING, Status.RUNNING, Status.FAILED}:
            raise InvalidTransitionError("Task", self.id, self.status.value, "test_bundle_frozen")
        if self.contract is None:
            raise ValueError("task contract must be frozen before its TestBundle")
        if not bundle.validates(self.id, self.revision):
            raise ValueError("TestBundle does not match the current task revision")
        criterion_ids = {item.id for item in self.contract.acceptance_criteria}
        if criterion_ids != set(bundle.criterion_to_tests):
            raise ValueError("every task criterion must map to authoritative checks")
        self.test_bundle = bundle
        self.verification_evidence = []

    def accept_verification(self, evidence: list[VerificationEvidence]) -> None:
        if self.test_bundle is None or not self.test_bundle.validates(self.id, self.revision):
            raise ValueError("current frozen TestBundle is required")
        if not evidence or not all(item.accepted for item in evidence):
            raise ValueError("independent accepted evidence is required")
        if any(
            item.task_id != self.id
            or item.task_revision != self.revision
            or item.test_commit_sha != self.test_bundle.test_commit_sha
            or item.exit_code != 0
            for item in evidence
        ):
            raise ValueError("verification evidence does not match task or tests")
        self.verification_evidence = list(evidence)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def is_ready_at(self, now: datetime) -> bool:
        return self.retry_not_before is None or self.retry_not_before <= now
