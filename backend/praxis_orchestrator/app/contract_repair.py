"""Repair a frozen contract that no agent could satisfy.

Two failure shapes, both produced by a real reasoner in a live Tier 1 run:

  * a verification command naming a path that does not exist while a near-twin
    does (`tests/test_greet.py` against `tests/test_greeter.py`);
  * a `tdd`/`characterization` contract whose `allowed_scope` contains no path
    the TEST-AUTHORING stage may write, so the first stage has nowhere legal to
    put the failing test.

Neither is the agent's fault and neither can be retried away — every attempt
fails identically until a human intervenes. Phase 1 rejects both at submission
time, so a fresh contract cannot be frozen this way; this is for contracts frozen
before that existed, and for repository drift, where a contract that was
satisfiable when written stops being so.

DETERMINISTIC on purpose — no second reasoner session. Both repairs are the exact
move an operator would make, both are derived from facts already in the
repository, and neither needs a provider call or a domain un-freeze. A repair
nobody can predict is not a repair, it is another guess; and the whole reason
these contracts are broken is that something guessed.

The repair only ever WIDENS scope and only ever rewrites a command token to a
path that exists. That keeps it non-invalidating (`Task.amend_contract`), so the
authored tests and any accepted evidence survive — a repair that re-authored the
test suite would cost more than the block it replaces.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Sequence

from praxis_orchestrator.app.verification import test_author_path_allowed
from praxis_orchestrator.domain.entities.execution_contracts import TaskContract

# A missing path with a close match is a typo; a missing path with none may be a
# file the task is about to create. Strict, because a false positive here rewrites
# a command that was correct.
_NEAR_MISS_CUTOFF = 0.8

# Rejections that point at the CONTRACT rather than at the candidate. Anything
# else — a promotion race, a provider limit, a genuine test failure — says
# nothing about whether the contract is satisfiable.
_CONTRACT_SHAPED = (
    "authoritative verification command failed",
    "test author modified production paths",
    "test author produced no executable checks",
    "path outside allowed scope",
)


@dataclass(frozen=True)
class ContractRepair:
    """A non-invalidating patch, in `Task.amend_contract`'s shape."""

    description: str
    allowed_scope: list[str] | None = None
    verification_commands: list[str] | None = None


def _looks_like_path(token: str) -> bool:
    if token.startswith("-") or "=" in token or any(ch in token for ch in "*$|><&"):
        return False
    if "/" not in token and "." not in token:
        return False
    return not token.replace(".", "").isdigit()


def _repair_commands(
    contract: TaskContract, tracked: Sequence[str]
) -> tuple[list[str] | None, str]:
    """Rewrite command tokens that name a near-twin of a real path."""
    known = set(tracked)
    directories = {path.rsplit("/", 1)[0] for path in tracked if "/" in path}
    repaired: list[str] = []
    notes: list[str] = []

    for command in contract.verification_commands:
        rebuilt = command
        for raw in command.split():
            token = raw.strip("\"'")
            if not _looks_like_path(token):
                continue
            normalized = token.strip("./")
            if normalized in known or normalized in directories:
                continue
            match = difflib.get_close_matches(normalized, tracked, n=1, cutoff=_NEAR_MISS_CUTOFF)
            if not match:
                continue
            rebuilt = rebuilt.replace(token, match[0])
            notes.append(f"{token} -> {match[0]}")
        repaired.append(rebuilt)

    if not notes:
        return None, ""
    return repaired, "rewrote " + ", ".join(notes)


def _repair_scope(contract: TaskContract, tracked: Sequence[str]) -> tuple[list[str] | None, str]:
    """Give a test-authoring stage somewhere legal to write."""
    # No strategy short-circuit. `executable_check` used to return early here on
    # the assumption that it has no authoring stage; it does whenever the
    # contract names no check that already exists, and then it needs somewhere
    # legal to write exactly like the others.
    if any(
        test_author_path_allowed(entry, contract.verification_strategy)
        for entry in contract.allowed_scope
    ):
        return None, ""

    # The repository's own test directory, not an invented one.
    candidates = sorted(
        {
            path.split("/", 1)[0] + "/"
            for path in tracked
            if "/" in path
            and test_author_path_allowed(path.split("/", 1)[0], contract.verification_strategy)
        }
    )
    if not candidates:
        return None, ""
    widened = [*contract.allowed_scope, candidates[0]]
    return widened, (
        f"added {candidates[0]} to allowed_scope so the "
        f"{contract.verification_strategy.value} test-authoring stage has somewhere to write"
    )


def propose_repair(
    contract: TaskContract,
    failure_reasons: Sequence[str],
    tracked_paths: Sequence[str],
) -> ContractRepair | None:
    """A patch that makes this contract satisfiable, or None.

    None is the common and correct answer: if the contract is fine, the agent's
    work was wrong, and rewriting a good contract to make a bad candidate pass is
    the one thing this must never do.
    """
    if not tracked_paths:
        return None  # every repair is derived from repository facts
    if not any(reason.strip().startswith(_CONTRACT_SHAPED) for reason in failure_reasons):
        return None

    commands, command_note = _repair_commands(contract, tracked_paths)
    scope, scope_note = _repair_scope(contract, tracked_paths)
    if commands is None and scope is None:
        return None
    return ContractRepair(
        description="; ".join(note for note in (command_note, scope_note) if note),
        allowed_scope=scope,
        verification_commands=commands,
    )


__all__ = ["ContractRepair", "propose_repair"]
