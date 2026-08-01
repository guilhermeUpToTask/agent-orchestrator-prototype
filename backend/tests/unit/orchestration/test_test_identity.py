"""Which checks belong to THIS task — resolved from the contract, never a scan.

The multi-task case is the reason this module exists. A repository scan for test
files cannot tell task 3's checks from task 1's, so on a goal with several tasks
it will freeze another task's failing test as this task's evidence. Declaring the
checks is the only thing that survives more than one task.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.app.test_identity import criterion_test_map, declared_checks
from agent_orchestrator.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    VerificationStrategy,
)


def contract(*commands: str, criteria: list[str] | None = None) -> TaskContract:
    return TaskContract(
        id="t1",
        position=0,
        objective="implement greet",
        acceptance_criteria=[
            ContractCriterion(id=c, description=c) for c in (criteria or ["c-1"])
        ],
        goal_criterion_ids=["g-1"],
        allowed_scope=["src/"],
        forbidden_scope=[],
        verification_commands=list(commands),
        verification_strategy=VerificationStrategy.EXECUTABLE_CHECK,
    )


def repo(tmp_path: Path, *relative: str) -> Path:
    for item in relative:
        path = tmp_path / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x(): pass\n")
    return tmp_path


# --- what counts as declared ----------------------------------------------


def test_a_named_check_file_is_declared(tmp_path: Path) -> None:
    root = repo(tmp_path, "tests/test_greeter.py")
    found = declared_checks(contract("python -m pytest -q tests/test_greeter.py"), root)

    assert found.declared
    assert found.files == ["tests/test_greeter.py"]


def test_a_node_id_keeps_its_selector_but_resolves_to_the_file(tmp_path: Path) -> None:
    """The node id is the precise mapping and belongs in `criterion_to_tests`;
    the FILE is what gets hashed, because you cannot hash half a file."""
    root = repo(tmp_path, "tests/test_greeter.py")
    found = declared_checks(
        contract("python -m pytest -q tests/test_greeter.py::test_greet"), root
    )

    assert found.node_ids == ["tests/test_greeter.py::test_greet"]
    assert found.files == ["tests/test_greeter.py"]


def test_a_bare_suite_invocation_is_not_declared(tmp_path: Path) -> None:
    """`pytest -q` selects every task's checks. Treating that as a declaration is
    exactly how task 3 inherits task 1's tests."""
    root = repo(tmp_path, "tests/test_greeter.py")
    assert not declared_checks(contract("python -m pytest -q"), root).declared


def test_a_directory_is_not_declared(tmp_path: Path) -> None:
    """Same collision as a bare invocation: `tests/` is every task's checks."""
    root = repo(tmp_path, "tests/test_greeter.py")
    assert not declared_checks(contract("python -m pytest -q tests/"), root).declared


def test_a_named_check_that_does_not_exist_is_not_declared(tmp_path: Path) -> None:
    """Greenfield: the contract names the file the author is about to create. It
    is not a declaration yet, so the author runs — the safe direction."""
    root = repo(tmp_path)
    assert not declared_checks(contract("python -m pytest -q tests/test_new.py"), root).declared


def test_production_code_named_in_a_command_is_not_a_check(tmp_path: Path) -> None:
    """`python src/main.py --selfcheck` runs the code; it does not select a
    check. Protecting production files would let the implementer's scope guard
    skip them."""
    root = repo(tmp_path, "src/main.py")
    assert not declared_checks(contract("python src/main.py --selfcheck"), root).declared


def test_flags_and_values_are_not_paths(tmp_path: Path) -> None:
    root = repo(tmp_path, "tests/test_greeter.py")
    found = declared_checks(
        contract("pytest -q --maxfail=1 --cov=src tests/test_greeter.py"), root
    )
    assert found.files == ["tests/test_greeter.py"]


def test_resolution_is_deterministic(tmp_path: Path) -> None:
    """The result becomes revision-bound evidence, so ordering must not depend on
    how the command happened to be written."""
    root = repo(tmp_path, "tests/test_b.py", "tests/test_a.py")
    found = declared_checks(contract("pytest -q tests/test_b.py tests/test_a.py"), root)
    assert found.files == ["tests/test_a.py", "tests/test_b.py"]


# --- the multi-task case this exists for ----------------------------------


def test_each_task_declares_only_its_own_checks(tmp_path: Path) -> None:
    """Three tasks, one repo. Task 3 must resolve to task 3's check and nothing
    else — not task 1's passing test, and not task 2's leftover red one. A
    repository scan cannot make this distinction; a declaration can.
    """
    root = repo(tmp_path, "tests/test_one.py", "tests/test_two.py", "tests/test_three.py")

    first = declared_checks(contract("pytest -q tests/test_one.py"), root)
    second = declared_checks(contract("pytest -q tests/test_two.py"), root)
    third = declared_checks(contract("pytest -q tests/test_three.py"), root)

    assert first.files == ["tests/test_one.py"]
    assert second.files == ["tests/test_two.py"]
    assert third.files == ["tests/test_three.py"]
    assert not set(third.files) & set(first.files) & set(second.files)


# --- the frozen mapping ----------------------------------------------------


def test_the_mapping_records_this_tasks_checks() -> None:
    mapping = criterion_test_map(
        contract("pytest -q tests/test_greeter.py", criteria=["c-1", "c-2"]),
        ["tests/test_greeter.py::test_greet"],
    )

    assert set(mapping) == {"c-1", "c-2"}
    assert mapping["c-1"] == ["tests/test_greeter.py::test_greet"]


def test_the_mapping_keys_stay_the_criterion_ids() -> None:
    """`Task.freeze_test_bundle` rejects a bundle whose key set is not exactly
    the contract's criteria, so this is load-bearing."""
    task_contract = contract("pytest -q tests/test_greeter.py", criteria=["a", "b", "c"])
    mapping = criterion_test_map(task_contract, ["tests/test_greeter.py"])

    assert set(mapping) == {item.id for item in task_contract.acceptance_criteria}
