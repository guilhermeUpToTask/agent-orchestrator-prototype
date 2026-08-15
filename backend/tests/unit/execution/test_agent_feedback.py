"""The A/B/C split that decides what reaches the next agent attempt's prompt.

Live Tier 1 run: attempt 1 hit a provider rate limit, attempt 2 failed
`test author produced no executable checks`, and the goal blocked. The agent was
never told anything, so a retry would have re-run an identical prompt against an
identical contract on a clean worktree — a guaranteed-identical failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from praxis_orchestrator.app.agent_feedback import is_agent_actionable, split_reasons

_SRC = Path(__file__).resolve().parents[3] / "praxis_orchestrator"


AGENT_REPAIRABLE = [
    "path outside allowed scope: .orchestrator/dry-run/t1.txt",
    "forbidden path changed: secrets/key.txt",
    "protected test changed: tests/test_behavior.py",
    "test bypass marker present: tests/test_behavior.py",
    "verification configuration changed: pyproject.toml",
    "test author produced no executable checks",
    "test author modified production paths: ['praxis_orchestrator/app.py']",
    "test bundle did not establish a meaningful RED result",
    "authoritative verification command failed",
    "verification command changed the validated candidate: path outside allowed scope: x",
]

NOT_AGENT_REPAIRABLE = [
    # orchestration invariants — races the agent has no business "fixing"
    "goal promotion targets a superseded cycle",
    "goal evidence changed during promotion",
    "goal cannot merge without accepted task evidence",
    "captured cycle 'c-1' no longer exists",
    "deterministic verification executor is unavailable",
    # provider capacity — says nothing about the work
    "rate limited by provider",
    "Upstream error from Nvidia: ResourceExhausted",
    None,
    "",
]


@pytest.mark.parametrize("message", AGENT_REPAIRABLE)
def test_candidate_rejections_are_fed_back(message: str) -> None:
    assert is_agent_actionable(message) is True


@pytest.mark.parametrize("message", NOT_AGENT_REPAIRABLE)
def test_races_infrastructure_and_capacity_are_not_fed_back(message: str | None) -> None:
    assert is_agent_actionable(message) is False


def test_joined_reasons_split_back_into_individual_items() -> None:
    joined = "path outside allowed scope: a.py; forbidden path changed: b.py"

    assert split_reasons(joined) == [
        "path outside allowed scope: a.py",
        "forbidden path changed: b.py",
    ]


def test_every_whitelisted_prefix_is_still_emitted_somewhere_in_src() -> None:
    """The whitelist is only honest while the strings it names still exist. If a
    rejection message is reworded and this list is not, the agent silently stops
    being told about that failure — the exact regression this module prevents.
    """
    from praxis_orchestrator.app.agent_feedback import _AGENT_ACTIONABLE_PREFIXES

    haystack = "\n".join(
        path.read_text()
        for path in (_SRC / "app").rglob("*.py")
        if path.name not in {"agent_feedback.py"}
    )
    missing = [prefix for prefix in _AGENT_ACTIONABLE_PREFIXES if prefix not in haystack]

    assert missing == [], f"whitelisted rejection wording no longer emitted: {missing}"


def test_the_whitelist_covers_every_candidate_rejection_the_handler_raises() -> None:
    """The other direction: a new candidate rejection added to the finalize path
    must be added here too, or the agent is never told about it."""
    from praxis_orchestrator.app.agent_feedback import _AGENT_ACTIONABLE_PREFIXES

    handler = (_SRC / "app" / "handlers" / "execution_handler.py").read_text()
    tree = ast.parse(handler)
    # the two finalize functions own every candidate rejection
    finalize = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"_finalize_test_author", "_finalize_verified_implementation"}
    ]
    assert finalize, "finalize functions not found — has the handler been restructured?"

    literals: list[str] = []
    for func in finalize:
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "TaskFailed"):
                continue
            first = node.args[0] if node.args else None
            for part in ast.walk(first) if first is not None else []:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    literals.append(part.value)

    uncovered = [
        text
        for text in literals
        if len(text) > 20 and not text.strip().startswith(_AGENT_ACTIONABLE_PREFIXES)
    ]
    assert uncovered == [], f"candidate rejections missing from the whitelist: {uncovered}"
