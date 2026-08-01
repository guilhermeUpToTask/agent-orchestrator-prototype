"""A block never advertises a resolution its own endpoint rejects.

`Plan.legal_actions` publishes a block's `legal_resolutions` verbatim to the API
with no endpoint hint, so an advertised-but-rejected resolution is the operator's
only obvious next step returning 422. Five live runs hit exactly that: an
`execution_failure` block offered `retry_stage`, and `POST /retry-stage` answered
`plan is not blocked on a retryable planning stage`.

These tests pin the whole surface: every kind constructed anywhere in the
backend is declared in `src/app/block_policy.py`, every call site draws from that
table, and every advertised resolution is reachable.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_orchestrator.app import block_policy
from agent_orchestrator.app.block_policy import (
    EDIT_TASK,
    RETRY_STAGE,
    START_REPLAN,
    WAIT_AND_RETRY,
    policy_for,
    resolutions_for,
)
from agent_orchestrator.domain.entities.planning_artifacts import PlanBlock

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

_SRC = Path(__file__).resolve().parents[3] / "src"

# Every resolution string the vocabulary allows, mapped to the use case that
# consumes it. A resolution with no consumer is a nominal-only advertisement.
_CONSUMERS = {
    RETRY_STAGE: ("retry_task", "retry_planning_stage"),
    WAIT_AND_RETRY: ("retry_task",),
    EDIT_TASK: ("apply_edit",),
    START_REPLAN: ("request_replan",),
    block_policy.BIND_PROJECT: ("bind_legacy_project",),
}


def _block(kind: str, **overrides: object) -> PlanBlock:
    values: dict[str, object] = {
        "id": "b-1",
        "kind": kind,
        "explanation": "boom",
        "stage": "implementation",
        "legal_resolutions": resolutions_for(kind),
        "created_at": NOW,
    }
    values.update(overrides)
    return PlanBlock(**values)  # type: ignore[arg-type]


def _constructed_kinds() -> set[str]:
    """Every `kind=` literal passed to a `PlanBlock(...)` construction in src/."""
    kinds: set[str] = set()
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "PlanBlock"):
                continue
            for keyword in node.keywords:
                if keyword.arg != "kind":
                    continue
                for literal in ast.walk(keyword.value):
                    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                        kinds.add(literal.value)
    return kinds


def test_every_constructed_block_kind_is_declared() -> None:
    """A kind invented at a call site has no declared resolutions and no
    human-intervention verdict — it is invisible to both the operator surface
    and to the roadmap that is removing unnecessary escalations."""
    undeclared = sorted(kind for kind in _constructed_kinds() if kind not in block_policy._BY_KIND)

    assert undeclared == [], (
        f"block kinds constructed in src/ but missing from block_policy: {undeclared}"
    )


def test_unknown_kind_raises_rather_than_defaulting() -> None:
    with pytest.raises(ValueError, match="unknown block kind"):
        policy_for("invented_kind")


@pytest.mark.parametrize("policy", block_policy._POLICIES, ids=lambda p: p.kind)
def test_every_advertised_resolution_has_a_consumer(policy: block_policy.BlockPolicy) -> None:
    for resolution in policy.resolutions:
        assert resolution in _CONSUMERS, (
            f"{policy.kind} advertises {resolution!r}, which no use case consumes"
        )


@pytest.mark.parametrize("policy", block_policy._POLICIES, ids=lambda p: p.kind)
def test_every_policy_states_why(policy: block_policy.BlockPolicy) -> None:
    """`requires_human` is a claim about the world; it has to carry its reason,
    because the roadmap flips these and a bare boolean cannot be reviewed."""
    assert policy.resolutions, f"{policy.kind} advertises nothing at all"
    assert len(policy.rationale) > 60, f"{policy.kind} rationale is too thin to review"


def test_narrowing_drops_retry_for_a_task_that_cannot_be_retried() -> None:
    """`Task.retry` guards FAILED -> PENDING. A goal wedged by a SKIPPED or
    evidence-less DONE task cannot be retried, so offering retry_stage there is
    a nominal-only resolution — the operator's click 422s."""
    assert resolutions_for("execution_failure") == [RETRY_STAGE, EDIT_TASK, START_REPLAN]
    assert resolutions_for("execution_failure", task_retryable=False) == [
        EDIT_TASK,
        START_REPLAN,
    ]
    # narrowing a kind that never offered retry_stage is a no-op, not an error
    assert resolutions_for("provider_capacity", task_retryable=False) == [
        WAIT_AND_RETRY,
        EDIT_TASK,
        START_REPLAN,
    ]


def test_a_site_may_narrow_but_never_widen() -> None:
    """The table is the ceiling. A site that adds a resolution of its own has
    escaped the single place where reachability is checked."""
    for policy in block_policy._POLICIES:
        narrowed = set(resolutions_for(policy.kind, task_retryable=False))
        assert narrowed <= set(policy.resolutions)


def test_block_kinds_that_should_not_need_a_human_are_recorded_as_such() -> None:
    """The design verdict, pinned so that removing an automatic recovery has to
    change this line too. These two are the escalations the roadmap removes:
    a verification failure that no agent was ever told about, and a goal merge
    that failed once because the cycle branch moved."""
    assert block_policy.requires_human("execution_failure") is False
    assert block_policy.requires_human("goal_promotion_failure") is False

    # And the genuinely human ones — information the system does not possess.
    assert block_policy.requires_human("project_binding") is True
    assert block_policy.requires_human("agent_capability") is True
    assert block_policy.requires_human("provider_capacity") is True
    assert block_policy.requires_human("reasoner_failure") is True


def test_policy_resolutions_match_what_the_domain_accepts_for_a_capacity_block() -> None:
    """`Plan.retry_task` gates on the intersection {retry_stage, wait_and_retry}
    (planner_orchestrator.py). A capacity block must carry one of them or
    `wait_and_retry` cannot clear the circuit it points at."""
    capacity = _block("provider_capacity", goal_id="g1", task_id="t1")

    assert {RETRY_STAGE, WAIT_AND_RETRY}.intersection(capacity.legal_resolutions)
