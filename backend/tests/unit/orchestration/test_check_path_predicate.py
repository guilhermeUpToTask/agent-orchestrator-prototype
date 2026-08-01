"""What counts as an executable check rather than production code.

`is_check_path` is the write-guard for the test-authoring stage: the RED stage
authors checks and must never touch production files. It is shared with the
reasoner's submission-time validation so a contract cannot freeze a strategy
whose first stage its own scope forbids.

Deliberately NOT tested here, because it deliberately does not exist: a
repository scan that discovers "the checks that already exist". One was written
while chasing a Tier 1 block and it is the wrong idea — a scan cannot tell task
3's checks from task 1's, so on a multi-task goal it freezes another task's
failing test as this task's evidence. Which tests prove a task is done is intent,
and intent has to be declared (`src/app/test_identity.py`).
"""

from __future__ import annotations

import pytest

from agent_orchestrator.app.verification import is_check_path

# Aliased: pytest collects any imported name starting with `test_` as a test
# case, and would report the function itself as an error for lacking a `path`
# fixture.
from agent_orchestrator.app.verification import test_author_path_allowed as author_path_allowed
from agent_orchestrator.domain.entities.execution_contracts import VerificationStrategy


@pytest.mark.parametrize(
    "path",
    ["tests/test_x.py", "pkg/tests/test_y.py", "test_top.py", "conftest.py", "pytest.ini"],
)
def test_check_paths_are_recognised(path: str) -> None:
    assert is_check_path(path)


@pytest.mark.parametrize("path", ["src/greeter.py", "README.md", "src/contest.py"])
def test_production_paths_are_not_checks(path: str) -> None:
    """`contest.py` is the interesting one: substring matching on "test" would
    protect production code and make every candidate fail its hash check."""
    assert not is_check_path(path)


@pytest.mark.parametrize("strategy", list(VerificationStrategy))
def test_the_authoring_stage_may_never_write_production_code(
    strategy: VerificationStrategy,
) -> None:
    """For EVERY strategy, including `executable_check`.

    That one used to return True unconditionally, on the assumption that it has
    no authoring stage. It does — whenever the contract names no check that
    already exists — and the blanket allow let the author write production code
    which was then hashed into `protected_file_hashes` as though it were a
    check, so the implementer's scope guard skipped it entirely.
    """
    assert not author_path_allowed("src/greeter.py", strategy)
    assert author_path_allowed("tests/test_x.py", strategy)
