from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_orchestrator.app.testing.fakes import InMemoryAgentRepository
from agent_orchestrator.app.verification import sha256_file, validate_candidate
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.capability import Capability
from agent_orchestrator.domain.entities.execution_contracts import (
    ContractCriterion,
    GoalContract,
    TaskContract,
    TestBundle as AuthoritativeTestBundle,
    TestBundleState as BundleState,
    VerificationStrategy,
)
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
from agent_orchestrator.domain.errors.agent_errors import RoleUnsatisfiableError
from agent_orchestrator.domain.errors.base import DomainError
from agent_orchestrator.domain.services.agent_role_resolution import (
    RunRole,
    resolve_role_agent,
    resolve_task_role_agents,
)
from agent_orchestrator.domain.value_objects.lifecycle import Status


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
    """Unsatisfiable role resolution must raise a CODED DomainError.

    It used to raise a bare `ValueError`, which carries no `code`, so the API's
    single `_STATUS_BY_CODE` table could not reach it: `POST /retry-stage` on an
    `agent_capability` block -- the block's OWN advertised legal resolution --
    returned an opaque `500 INTERNAL_ERROR` instead of telling the operator
    which capabilities no agent covers. agent_orchestrator/api/exceptions.py states the rule
    outright: "an unmapped builtin error is a bug and should surface as the
    enveloped 500."
    """
    repository = InMemoryAgentRepository([agent("default", ["python"])], "default")
    with pytest.raises(RoleUnsatisfiableError, match="test_author") as excinfo:
        resolve_role_agent(RunRole.TEST_AUTHOR, ["python"], repository)

    assert excinfo.value.code == "ROLE_UNSATISFIABLE"
    assert isinstance(excinfo.value, DomainError)
    # The operator needs to know exactly what to register.
    assert set(excinfo.value.required) == {"test_authoring", "python"}


def test_role_resolution_does_not_require_a_default_agent() -> None:
    repository = InMemoryAgentRepository(
        [agent("tests", ["test_authoring", "python"])],
        default_id=None,
    )

    assert resolve_role_agent(RunRole.TEST_AUTHOR, ["python"], repository).id == "tests"


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


def test_repository_root_scope_accepts_normal_relative_paths(tmp_path) -> None:
    protected = tmp_path / "tests" / "test_behavior.py"
    protected.parent.mkdir()
    protected.write_text("def test_behavior():\n    assert True\n")
    frozen = bundle("tests/test_behavior.py", sha256_file(protected))
    contract = task_contract(allowed_scope=["."], forbidden_scope=[".git/"])

    accepted = validate_candidate(tmp_path, contract, frozen, ["agent_orchestrator/app.py"])
    rejected = validate_candidate(tmp_path, contract, frozen, [".git/config"])

    assert accepted.accepted
    assert not rejected.accepted
    assert rejected.reasons == ("forbidden path changed: .git/config",)


def test_dot_prefixed_scope_does_not_match_a_non_dot_directory(tmp_path) -> None:
    protected = tmp_path / "tests" / "test_behavior.py"
    protected.parent.mkdir()
    protected.write_text("def test_behavior():\n    assert True\n")
    frozen = bundle("tests/test_behavior.py", sha256_file(protected))
    contract = task_contract(allowed_scope=[".config/"])

    result = validate_candidate(tmp_path, contract, frozen, ["config/settings.json"])

    assert not result.accepted
    assert result.reasons == ("path outside allowed scope: config/settings.json",)


def role_agent(agent_id: str, role: str, capability_ids: list[str]) -> AgentSpec:
    """Like `agent`, but with a DECLARED role rather than the neutral placeholder."""
    return agent(agent_id, capability_ids).model_copy(update={"role": role})


def test_the_implementer_role_is_never_bound_to_a_test_author() -> None:
    """The P8.4 demo defect, reproduced exactly.

    A TDD task declares BOTH `test_authoring` and `implementation`, because it
    has both stages. Role resolution used to union the ROLE's capability with
    the TASK's whole list, so resolving IMPLEMENTER demanded an agent that could
    also author tests — and the only agents that qualify are precisely the ones
    whose instructions say "Do NOT implement the feature". The live run bound
    both roles to `test-agent` and the GREEN stage could never succeed.
    """
    repository = InMemoryAgentRepository(
        [
            role_agent("tests", "test_author", ["test_authoring", "implementation", "python"]),
            role_agent("impl", "implementer", ["implementation", "python"]),
        ],
        default_id="tests",
    )
    task_capabilities = ["test_authoring", "implementation", "python"]

    assert resolve_role_agent(RunRole.TEST_AUTHOR, task_capabilities, repository).id == "tests"
    assert resolve_role_agent(RunRole.IMPLEMENTER, task_capabilities, repository).id == "impl"


def test_a_declared_test_author_is_never_bound_to_the_implementer_role() -> None:
    """Deterministic, not "preferred": a contradicting role is NEVER selected.

    An agent that calls itself a `test_author` is not an implementer of last
    resort. Binding one anyway does not degrade gracefully — it fails as an
    agent-quality problem three layers from the cause, which is what made the
    P8.4 defect cost a full cycle to find. `RoleUnsatisfiableError` opens an
    `agent_capability` block that names the gap immediately and is already
    resolvable from the API.
    """
    lone_author = role_agent("tests", "test_author", ["test_authoring", "implementation", "python"])
    repository = InMemoryAgentRepository([lone_author], default_id="tests")

    with pytest.raises(RoleUnsatisfiableError, match="implementer"):
        resolve_role_agent(RunRole.IMPLEMENTER, ["implementation", "python"], repository)


def test_seed_demo_registers_a_registry_that_satisfies_both_roles() -> None:
    """The reason the resolver can afford to be strict.

    A single agent holding every capability is what forced the permissive
    fallback in the first place. The default installation now registers a real
    pair, so both roles resolve honestly rather than by weakening the rule.
    """
    repository = InMemoryAgentRepository(
        [
            role_agent("dev-agent", "implementer", ["backend", "testing", "implementation"]),
            role_agent("test-agent", "test_author", ["backend", "testing", "test_authoring"]),
        ],
        default_id="dev-agent",
    )
    caps = ["test_authoring", "implementation", "backend"]

    assert resolve_task_role_agents(caps, repository) == {
        "test_author": "test-agent",
        "implementer": "dev-agent",
    }


def test_role_resolution_still_blocks_when_no_agent_has_the_capability() -> None:
    """The fallback ladder must not swallow a genuinely unsatisfiable role."""
    repository = InMemoryAgentRepository(
        [role_agent("tests", "test_author", ["test_authoring"])], default_id="tests"
    )

    with pytest.raises(RoleUnsatisfiableError, match="implementer"):
        resolve_role_agent(RunRole.IMPLEMENTER, ["implementation"], repository)


def test_an_agent_with_no_declared_run_role_is_a_usable_fallback() -> None:
    """The edge case that must NOT become a block: a registry of neutrally
    labelled agents (the `seed demo` shape, role="configured") still resolves.
    Only a CONTRADICTING declared role disqualifies."""
    repository = InMemoryAgentRepository(
        [agent("generalist", ["test_authoring", "implementation", "python"])],
        default_id="generalist",
    )
    caps = ["test_authoring", "implementation", "python"]

    assert resolve_role_agent(RunRole.TEST_AUTHOR, caps, repository).id == "generalist"
    assert resolve_role_agent(RunRole.IMPLEMENTER, caps, repository).id == "generalist"


def test_a_declared_role_beats_a_neutral_agent_regardless_of_registry_order() -> None:
    """Deterministic: the binding must not depend on who was registered first."""
    specialist = role_agent("impl", "implementer", ["implementation", "python"])
    neutral = agent("generalist", ["test_authoring", "implementation", "python"])

    for catalog in ([neutral, specialist], [specialist, neutral]):
        repository = InMemoryAgentRepository(list(catalog), default_id="generalist")
        chosen = resolve_role_agent(RunRole.IMPLEMENTER, ["implementation", "python"], repository)
        assert chosen.id == "impl"
