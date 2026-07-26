from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    TestBundle,
    VerificationEvidence,
    VerificationStrategy,
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

    def semantic_edit(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        acceptance_criteria: list[ContractCriterion] | None = None,
        verification_strategy: VerificationStrategy | None = None,
    ) -> None:
        """Revise executable meaning and invalidate revision-bound artifacts.

        Un-freeze #17 widened this to the contract fields that change what
        "correct" MEANS. Each one makes the authored tests stop describing the
        task: criteria are what `freeze_test_bundle` maps to checks, and the
        strategy decides what evidence is even meaningful. Fields that only
        change how correctness is CHECKED go to `amend_contract` instead, which
        keeps the tests — re-authoring a suite to fix a typo in a command is what
        made repairing a contract cost a whole replan.
        """
        self._guard({Status.PENDING, Status.FAILED}, Status.PENDING)
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        self.status = Status.PENDING
        self.result = None
        self.revision += 1
        if self.contract is not None:
            update: dict[str, object] = {
                "revision": self.revision,
                "objective": self.description or self.name,
            }
            if acceptance_criteria is not None:
                update["acceptance_criteria"] = acceptance_criteria
            if verification_strategy is not None:
                update["verification_strategy"] = verification_strategy
            # A full revalidation, not a blind copy: an edit must not be able to
            # write a contract shape enrichment itself could never produce.
            self.contract = TaskContract.model_validate(
                {**self.contract.model_dump(), **update}
            )
        if self.test_bundle is not None:
            self.test_bundle.invalidate("semantic task edit")
        self.verification_evidence = []
        self.cycle_attempt = 0
        self.retry_not_before = None

    def amend_contract(
        self,
        *,
        allowed_scope: list[str] | None = None,
        forbidden_scope: list[str] | None = None,
        verification_commands: list[str] | None = None,
        goal_criterion_ids: list[str] | None = None,
        required_capabilities: list[str] | None = None,
    ) -> None:
        """Repair HOW correctness is checked, without re-authoring the tests.

        Deliberately does NOT bump the revision or touch the `TestBundle`: none
        of these fields changes what the task must do, so the authored tests
        still describe it. That is the whole point — the live repair was a
        verification command naming `tests/test_greet.py` in a repository whose
        file is `tests/test_greeter.py`, and paying for a full test re-authoring
        to fix one filename is why a replan looked cheaper than an edit.

        Accepted evidence is cleared only when a previously accepted candidate
        might no longer qualify: the commands changed (so the recorded runs are
        stale), or the scope NARROWED (so an accepted candidate may have touched
        a path that is now excluded). Widening is provably safe and keeps it.
        """
        self._guard({Status.PENDING, Status.FAILED}, Status.PENDING)
        if self.contract is None:
            raise ValueError("task has no contract to amend")

        update: dict[str, object] = {}
        if allowed_scope is not None:
            update["allowed_scope"] = allowed_scope
        if forbidden_scope is not None:
            update["forbidden_scope"] = forbidden_scope
        if verification_commands is not None:
            update["verification_commands"] = verification_commands
        if goal_criterion_ids is not None:
            update["goal_criterion_ids"] = goal_criterion_ids
        if required_capabilities is not None:
            update["required_capabilities"] = required_capabilities
        if not update:
            return

        narrowed = allowed_scope is not None and not set(allowed_scope) >= set(
            self.contract.allowed_scope
        )
        forbade_more = forbidden_scope is not None and not set(forbidden_scope) <= set(
            self.contract.forbidden_scope
        )
        commands_changed = (
            verification_commands is not None
            and verification_commands != self.contract.verification_commands
        )

        self.contract = TaskContract.model_validate({**self.contract.model_dump(), **update})
        self.status = Status.PENDING
        self.result = None
        self.cycle_attempt = 0
        self.retry_not_before = None
        if narrowed or forbade_more or commands_changed:
            self.verification_evidence = []

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
