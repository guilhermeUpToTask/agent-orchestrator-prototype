"""A merge that failed for a transient reason is not a decision for a human.

`goal_promotion_failure` opened on the FIRST exception from `merge_goal`, with no
retry at all, and advertised a single resolution: `start_replan`. So a git
worktree still being cleaned up, or a lock held for a moment by another worker,
threw away a fully verified goal and asked a person to regenerate the cycle.

A real merge CONFLICT is different in kind — two goals genuinely changed the same
lines, and no retry resolves that. The classification is what makes the retry
safe, so it is pinned here rather than left to a substring guess at the call site.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.app.promotion_failures import is_transient_merge_failure

CONFLICTS = [
    "CONFLICT (content): Merge conflict in agent_orchestrator/app.py",
    "Automatic merge failed; fix conflicts and then commit the result.",
    "error: Your local changes to the following files would be overwritten by merge",
    "goal branch is missing: goal/abc",
]

TRANSIENT = [
    "fatal: 'cycle-merge-xyz' is already checked out at '/tmp/cycle-merge-xyz'",
    "fatal: Unable to create '/repo/.git/index.lock': File exists.",
    "error: unable to write file agent_orchestrator/app.py: No space left on device",
    "fatal: could not read Username for 'https://github.com': No such device",
    "OSError: [Errno 5] Input/output error",
]


@pytest.mark.parametrize("message", CONFLICTS)
def test_a_real_conflict_is_permanent(message: str) -> None:
    """Retrying a conflict burns a worktree and changes nothing."""
    assert is_transient_merge_failure(message) is False


@pytest.mark.parametrize("message", TRANSIENT)
def test_an_environment_failure_is_transient(message: str) -> None:
    assert is_transient_merge_failure(message) is True


def test_an_unrecognised_failure_is_treated_as_permanent() -> None:
    """Fail closed. A retry loop on an unknown error is how a plan burns a
    worker forever; a spurious block is merely annoying, and the operator still
    sees the message."""
    assert is_transient_merge_failure("something nobody has seen before") is False


def test_classification_is_case_insensitive_and_survives_wrapping() -> None:
    wrapped = "CalledProcessError: fatal: Unable to create '/x/.git/INDEX.LOCK': File exists."

    assert is_transient_merge_failure(wrapped) is True
