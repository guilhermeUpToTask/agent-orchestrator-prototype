from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.app.testing.fakes import InMemoryAgentRepository
from src.app.verification import sha256_file, validate_candidate
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.execution_contracts import (
    ContractCriterion,
    GoalContract,
    TaskContract,
    TestBundle as AuthoritativeTestBundle,
    TestBundleState as BundleState,
    VerificationStrategy,
)
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.services.agent_role_resolution import RunRole, resolve_role_agent
from src.domain.value_objects.lifecycle import Status


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def task_contract(**updates: object) -> TaskContract:
    values: dict[str, object] = {
        "id": "task-1",
        "position": 0,
        "objective": "implement behavior",
        "acceptance_criteria": [ContractCriterion(id="t-1", description="works")],
        "goal_criterion_ids": ["g-1"],
        "allowed_scope": ["src/"],
        "forbidden_scope": ["secrets/"],
        "verification_commands": ["pytest tests/test_behavior.py"],
        "verification_strategy": VerificationStrategy.TDD,
        "required_capabilities": ["python"],
    }
    values.update(updates)
    return TaskContract(**values)


def bundle(path: str, digest: str, revision: int = 1) -> AuthoritativeTestBundle:
    return AuthoritativeTestBundle(
        task_id="task-1",
        task_revision=revision,
        test_commit_sha="abc",
        protected_file_hashes={path: digest},
        criterion_to_tests={"t-1": ["test_behavior"]},
        verification_strategy=VerificationStrategy.TDD,
        red_or_baseline_evidence_refs=["artifact://red"],
        frozen_at=NOW,
    )


def agent(agent_id: str, capability_ids: list[str]) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role="configured",
        model_role="smart",
        instructions="",
        capabilities=[
            Capability(id=capability_id, name=capability_id, description="")
            for capability_id in capability_ids
        ],
        default_retry=RetryPolicy(),
    )


def test_goal_contract_requires_complete_criterion_coverage() -> None:
    GoalContract(
        id="goal-1",
        objective="goal",
        acceptance_criteria=[ContractCriterion(id="g-1", description="covered")],
        tasks=[task_contract()],
        frozen_at=NOW,
    )
    with pytest.raises(ValidationError, match="uncovered goal criteria"):
        GoalContract(
            id="goal-1",
            objective="goal",
            acceptance_criteria=[
                ContractCriterion(id="g-1", description="covered"),
                ContractCriterion(id="g-2", description="missing"),
            ],
            tasks=[task_contract()],
            frozen_at=NOW,
        )


def test_goal_contract_rejects_duplicate_task_ids() -> None:
    with pytest.raises(ValidationError, match="task ids must be unique"):
        GoalContract(
            id="goal-1",
            objective="goal",
            acceptance_criteria=[ContractCriterion(id="g-1", description="covered")],
            tasks=[
                task_contract(),
                task_contract(position=1),
            ],
            frozen_at=NOW,
        )


def test_semantic_edit_invalidates_bundle_and_old_evidence() -> None:
    task = Task(
        id="task-1",
        name="old",
        position=0,
        description="old contract",
        status=Status.FAILED,
        contract=task_contract(),
        test_bundle=bundle("tests/test_behavior.py", "old"),
        verification_evidence=[],
    )
    task.semantic_edit(description="new contract")

    assert task.revision == 2
    assert task.contract is not None and task.contract.revision == 2
    assert task.test_bundle is not None
    assert task.test_bundle.state == BundleState.INVALID
    assert not task.test_bundle.validates(task.id, task.revision)


def test_existing_registry_resolves_separate_roles_by_capability() -> None:
    repository = InMemoryAgentRepository(
        [
            agent("tests", ["test_authoring", "python"]),
            agent("impl", ["implementation", "python"]),
            agent("both", ["test_authoring", "implementation", "python"]),
        ],
        default_id="both",
    )
    assert resolve_role_agent(RunRole.TEST_AUTHOR, ["python"], repository).id == "tests"
    assert resolve_role_agent(RunRole.IMPLEMENTER, ["python"], repository).id == "impl"


def test_role_resolution_never_falls_back_without_role_capability() -> None:
    repository = InMemoryAgentRepository([agent("default", ["python"])], "default")
    with pytest.raises(ValueError, match="test_author"):
        resolve_role_agent(RunRole.TEST_AUTHOR, ["python"], repository)


def test_protected_test_and_scope_enforcement(tmp_path) -> None:
    protected = tmp_path / "tests" / "test_behavior.py"
    protected.parent.mkdir()
    protected.write_text("def test_behavior():\n    assert True\n")
    frozen = bundle("tests/test_behavior.py", sha256_file(protected))
    contract = task_contract()

    accepted = validate_candidate(tmp_path, contract, frozen, ["src/feature.py"])
    assert accepted.accepted

    protected.write_text("@pytest.mark.skip\ndef test_behavior():\n    assert True\n")
    rejected = validate_candidate(
        tmp_path,
        contract,
        frozen,
        ["tests/test_behavior.py", "secrets/key.txt", "pyproject.toml"],
    )
    assert not rejected.accepted
    assert any("protected test changed" in reason for reason in rejected.reasons)
    assert any("bypass marker" in reason for reason in rejected.reasons)
    assert any("forbidden path" in reason for reason in rejected.reasons)
    assert any("configuration changed" in reason for reason in rejected.reasons)
