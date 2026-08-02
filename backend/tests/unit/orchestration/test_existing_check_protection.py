"""Checks that already exist are identified, protected, and never claimed.

The hole this closes, on a multi-task goal: task 2's author rewrites task 1's
check into something trivially failing. It is a check path, so the old
`is_check_path`-only guard allowed it; it appeared in the diff, so it became
*task 2's* protected evidence; the implementer then made it pass. Task 1's
verification was silently replaced and every gate downstream reported green.

The mechanism is a snapshot taken BEFORE the author runs. It answers "what checks
exist right now" — a fact about the tree — and never "which of them are mine",
which is intent and cannot be inferred.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_orchestrator.app.test_identity import existing_checks
from agent_orchestrator.app.verification import (
    baseline_outcome,
    check_config_untouched,
    check_declared_scope,
    check_protected,
    is_byproduct_path,
    sha256_file,
    validate_authoring,
)
from agent_orchestrator.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    VerificationStrategy,
)


def write(root: Path, relative: str, text: str = "def test_x():\n    assert True\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def contract(*, allowed: list[str], forbidden: list[str] | None = None) -> TaskContract:
    return TaskContract(
        id="t1",
        position=0,
        objective="o",
        acceptance_criteria=[ContractCriterion(id="c-1", description="c")],
        goal_criterion_ids=["g-1"],
        allowed_scope=allowed,
        forbidden_scope=forbidden or [],
        verification_commands=["pytest -q"],
        verification_strategy=VerificationStrategy.TDD,
    )


# --- the snapshot ----------------------------------------------------------


def test_existing_checks_are_hashed_and_production_code_is_not(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_one.py")
    write(tmp_path, "src/greeter.py", "def greet(n): ...\n")

    found = existing_checks(tmp_path)

    assert set(found) == {"tests/test_one.py"}
    assert found["tests/test_one.py"] == sha256_file(tmp_path / "tests/test_one.py")


def test_byproducts_are_never_protected(tmp_path: Path) -> None:
    """`tests/__pycache__/test_one.cpython-311.pyc` matches `is_check_path` on its
    basename, and a `.pyc` embeds the source mtime — so protecting one makes every
    later candidate fail a hash it cannot reproduce."""
    write(tmp_path, "tests/test_one.py")
    write(tmp_path, "tests/__pycache__/test_one.cpython-311.pyc", "bytes")
    write(tmp_path, "node_modules/pkg/test_vendor.js", "it()")
    write(tmp_path, ".venv/lib/test_dep.py", "")

    assert set(existing_checks(tmp_path)) == {"tests/test_one.py"}


def test_vendored_directories_are_pruned_not_filtered(tmp_path: Path) -> None:
    """Pruning matters for speed, not just correctness: a post-filter still walks
    every file in `node_modules` first."""
    deep = tmp_path / "node_modules" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "test_deep.js").write_text("it()")
    write(tmp_path, "tests/test_one.py")

    assert set(existing_checks(tmp_path)) == {"tests/test_one.py"}


def test_symlinks_are_skipped(tmp_path: Path) -> None:
    """A link loop would hang the walk, and a link out of the repository would
    hash a file this run does not own."""
    write(tmp_path, "tests/test_one.py")
    outside = tmp_path.parent / "outside_test_x.py"
    outside.write_text("def test_outside(): pass\n")
    os.symlink(outside, tmp_path / "tests" / "test_linked.py")
    os.symlink(tmp_path / "tests", tmp_path / "tests" / "loop")

    found = existing_checks(tmp_path)

    assert set(found) == {"tests/test_one.py"}


def test_an_empty_repository_protects_nothing(tmp_path: Path) -> None:
    """Greenfield: everything the author writes is new, so everything is theirs."""
    write(tmp_path, "src/greeter.py", "")
    assert existing_checks(tmp_path) == {}


def test_the_snapshot_is_ordered(tmp_path: Path) -> None:
    for name in ("test_c.py", "test_a.py", "test_b.py"):
        write(tmp_path, f"tests/{name}")
    assert list(existing_checks(tmp_path)) == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "tests/__pycache__/test_x.cpython-311.pyc",
        "src/mod.pyo",
        "node_modules/p/test_a.js",
        ".venv/lib/test_b.py",
        "pkg.egg-info/test_c.py",
        ".coverage.host.1",
    ],
)
def test_byproduct_predicate(path: str) -> None:
    assert is_byproduct_path(path)


# --- the showstopper -------------------------------------------------------


def test_an_untouched_pre_existing_skip_is_not_a_rejection(tmp_path: Path) -> None:
    """THE regression this file exists for.

    The bypass-marker scan used to run on every protected file unconditionally.
    Once checks that were already in the repository became protected, any repo
    containing `@pytest.mark.skip`, `@pytest.mark.xfail`, or even the substring
    `.skip(` would reject every candidate forever. Real repositories have skipped
    tests; this must be silent.
    """
    skipped = write(
        tmp_path,
        "tests/test_legacy.py",
        "import pytest\n\n\n@pytest.mark.skip(reason='known flake')\ndef test_x():\n    pass\n",
    )
    protected = {"tests/test_legacy.py": sha256_file(skipped)}

    assert check_protected(tmp_path, protected) == []


def test_a_marker_added_to_a_modified_check_is_a_rejection(tmp_path: Path) -> None:
    """The scan still does its job where it means something: the file CHANGED and
    the change inserted a bypass."""
    original = write(tmp_path, "tests/test_one.py", "def test_x():\n    assert 1 == 1\n")
    protected = {"tests/test_one.py": sha256_file(original)}
    original.write_text("import pytest\n\n\n@pytest.mark.skip\ndef test_x():\n    pass\n")

    reasons = check_protected(tmp_path, protected)

    assert any("protected test changed" in reason for reason in reasons)
    assert any("bypass marker present" in reason for reason in reasons)


def test_a_deleted_protected_check_is_a_rejection(tmp_path: Path) -> None:
    original = write(tmp_path, "tests/test_one.py")
    protected = {"tests/test_one.py": sha256_file(original)}
    original.unlink()

    assert check_protected(tmp_path, protected) == [
        "protected test missing or renamed: tests/test_one.py"
    ]


# --- the attack ------------------------------------------------------------


def test_rewriting_another_tasks_check_is_rejected(tmp_path: Path) -> None:
    """Task 2's author cannot claim task 1's check by editing it."""
    task_one = write(tmp_path, "tests/test_one.py", "def test_one():\n    assert greet() == 'hi'\n")
    before = existing_checks(tmp_path)
    task_one.write_text("def test_one():\n    assert False\n")  # trivially failing

    verdict = validate_authoring(tmp_path, before, ["tests/test_one.py"])

    assert not verdict.accepted
    assert any("protected test changed: tests/test_one.py" in r for r in verdict.reasons)


def test_adding_a_new_check_beside_another_tasks_is_allowed(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_one.py")
    before = existing_checks(tmp_path)
    write(tmp_path, "tests/test_two.py")

    assert validate_authoring(tmp_path, before, ["tests/test_two.py"]).accepted


def test_the_author_may_not_move_the_verification_configuration(tmp_path: Path) -> None:
    """Rewriting `pyproject.toml` can disable collection entirely, which makes a
    green result meaningless regardless of which stage did it."""
    before = existing_checks(tmp_path)
    verdict = validate_authoring(tmp_path, before, ["tests/test_new.py", "pyproject.toml"])

    assert not verdict.accepted
    assert any("verification configuration changed: pyproject.toml" in r for r in verdict.reasons)


# --- scope is stage-specific ----------------------------------------------


def test_authoring_is_not_judged_by_the_implementers_scope(tmp_path: Path) -> None:
    """The conventional way to keep the implementer out of the tests is
    `forbidden_scope: ["tests/"]` — which is exactly where the author must write.
    The two stages have mirror-image legal areas, so applying one scope check to
    both would reject every correct TDD contract."""
    task = contract(allowed=["feature.txt"], forbidden=["tests/"])

    # The implementer may not touch tests/ ...
    assert check_declared_scope(task, ["tests/test_feature.py"], set()) != []
    # ... but the author writing exactly that is fine.
    assert validate_authoring(tmp_path, {}, ["tests/test_feature.py"]).accepted


def test_the_config_guard_is_shared_by_both_stages(tmp_path: Path) -> None:
    assert check_config_untouched(["pytest.ini"], set()) != []
    assert check_config_untouched(["src/greeter.py"], set()) == []
    assert check_config_untouched(["pytest.ini"], {"pytest.ini"}) == [], (
        "a protected path is covered by its hash, not re-judged here"
    )


# --- the baseline rule, one definition ------------------------------------


@pytest.mark.parametrize(
    ("strategy", "codes", "accepted", "verdict"),
    [
        # tdd and executable_check differ only in who typed the test, so both
        # demand a discriminating (failing) baseline.
        (VerificationStrategy.TDD, [1], True, "red"),
        (VerificationStrategy.TDD, [0], False, "green"),
        (VerificationStrategy.EXECUTABLE_CHECK, [1], True, "red"),
        (VerificationStrategy.EXECUTABLE_CHECK, [0], False, "green"),
        (VerificationStrategy.TDD, [0, 1], True, "red"),
        # characterization pins behaviour that already works.
        (VerificationStrategy.CHARACTERIZATION, [0], True, "green"),
        (VerificationStrategy.CHARACTERIZATION, [1], False, "red"),
        (VerificationStrategy.CHARACTERIZATION, [0, 1], False, "red"),
    ],
)
def test_baseline_rule(
    strategy: VerificationStrategy, codes: list[int], accepted: bool, verdict: str
) -> None:
    outcome = baseline_outcome(strategy, codes)
    assert outcome.accepted is accepted
    assert outcome.verdict == verdict


@pytest.mark.parametrize("strategy", list(VerificationStrategy))
def test_no_commands_is_never_an_acceptable_baseline(strategy: VerificationStrategy) -> None:
    """A stage that ran nothing measured nothing. `green` is the honest verdict
    for an empty run, but it must never be accepted as evidence."""
    outcome = baseline_outcome(strategy, [])
    assert not outcome.accepted
