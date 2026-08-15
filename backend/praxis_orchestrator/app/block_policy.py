"""The one table that decides what a `PlanBlock` may advertise.

A block is the orchestrator telling a human "I cannot continue without you". It
is legitimate ONLY when resolving it needs information the system does not
possess — which project a plan belongs to, which agent to register, which
credential to fix, what the product should do. Everything else is waiting or
automatic repair; the model to copy is provider capacity, which waits on a
wall-clock ceiling with a single-flight probe and blocks only past it.

Two rules, both enforced by `tests/unit/orchestration/test_block_policy.py`:

1. A block never advertises a resolution its own endpoint rejects. Every entry
   in `resolutions` must be accepted by the route that serves it, for that kind.
   Advertising a resolution that 422s is worse than advertising none: the
   operator's only visible next step fails, and `Plan.legal_actions` publishes
   the raw strings to the API with no endpoint hint.
2. A call site may NARROW the advertised set for the state at hand (a task that
   is not FAILED cannot be retried) but may never widen it beyond this table.

`requires_human` is the DESIGN verdict, not a description of today's behaviour:
every kind here currently opens a block on its terminal path. The two entries
marked False are the ones where that is a defect — the roadmap replaces them
with a bounded automatic recovery plus a backstop block past the bound. Flipping
one to False without shipping that recovery would just make the table lie.
"""

from __future__ import annotations

from dataclasses import dataclass

# The resolution vocabulary. Each is served by exactly one route:
#   retry_stage    -> POST /api/plans/{id}/retry-stage   (planning stages)
#                     POST /api/plans/{id}/retry         (a named failed task)
#   wait_and_retry -> POST /api/plans/{id}/retry         (clears the circuit)
#   edit_task      -> POST /api/plans/{id}/edits         (recovery window)
#   start_replan   -> POST /api/plans/{id}/replan
#   bind_project   -> POST /api/plans/{id}/project-binding
RETRY_STAGE = "retry_stage"
WAIT_AND_RETRY = "wait_and_retry"
EDIT_TASK = "edit_task"
START_REPLAN = "start_replan"
BIND_PROJECT = "bind_project"


@dataclass(frozen=True)
class BlockPolicy:
    kind: str
    requires_human: bool
    resolutions: tuple[str, ...]
    rationale: str


_POLICIES: tuple[BlockPolicy, ...] = (
    BlockPolicy(
        kind="project_binding",
        requires_human=True,
        resolutions=(BIND_PROJECT,),
        rationale=(
            "Only a human knows which project a pre-cyclic plan row belongs to. "
            "The identity is not derivable from anything the orchestrator stores."
        ),
    ),
    BlockPolicy(
        kind="agent_capability",
        requires_human=True,
        resolutions=(RETRY_STAGE, START_REPLAN),
        rationale=(
            "No registered agent satisfies a task's required capabilities. The "
            "agent registry is outside the system; nothing but registry repair "
            "fixes it. retry_stage re-resolves the bindings once that is done."
        ),
    ),
    BlockPolicy(
        kind="reasoner_failure",
        requires_human=True,
        resolutions=(RETRY_STAGE, START_REPLAN),
        rationale=(
            "Reached only after the planning backoff gate is exhausted, or "
            "immediately for a permanent kind (auth, or a model that cannot call "
            "tools). Waiting longer cannot fix either."
        ),
    ),
    BlockPolicy(
        kind="provider_capacity",
        requires_human=True,
        resolutions=(WAIT_AND_RETRY, EDIT_TASK, START_REPLAN),
        rationale=(
            "Reached only past the wall-clock outage ceiling, where the "
            "signature is indistinguishable from a revoked key or a wrong "
            "base_url — those look transient forever. The wait already happened."
        ),
    ),
    BlockPolicy(
        kind="goal_promotion_failure",
        requires_human=False,
        resolutions=(START_REPLAN,),
        rationale=(
            "A merge CONFLICT is a genuine decision: two goals changed the same "
            "lines and no retry resolves it. An ENVIRONMENTAL failure is not — a "
            "stale worktree registration or a held index lock clears on its own, "
            "and those are now released and re-attempted (bounded) instead of "
            "throwing away a verified goal. What is still unaddressed is a merge "
            "against a cycle branch that MOVED; rebasing for that would also have "
            "to introduce goal-level re-verification, which does not exist, so it "
            "waits on run evidence (see the ROADMAP deferral)."
        ),
    ),
    BlockPolicy(
        kind="execution_failure",
        requires_human=False,
        resolutions=(RETRY_STAGE, EDIT_TASK, START_REPLAN),
        rationale=(
            "The largest unnecessary-escalation surface. A candidate that fails "
            "verification is discarded and blocks after ONE attempt, because "
            "VERIFICATION_ERROR is non-retryable — and the next agent run would "
            "receive no feedback anyway. With the rejection reasons fed back into "
            "the prompt, and contract repair for a contract no agent could "
            "satisfy, this needs a human only as a backstop."
        ),
    ),
)

_BY_KIND: dict[str, BlockPolicy] = {policy.kind: policy for policy in _POLICIES}


def policy_for(kind: str) -> BlockPolicy:
    """The policy for a block kind. An unknown kind is a bug, not a default."""
    try:
        return _BY_KIND[kind]
    except KeyError:
        raise ValueError(
            f"unknown block kind {kind!r}; add it to praxis_orchestrator/app/block_policy.py "
            "so its resolutions and human-intervention verdict are declared once"
        ) from None


def resolutions_for(kind: str, *, task_retryable: bool | None = None) -> list[str]:
    """The resolutions this block may advertise, narrowed for the state at hand.

    `task_retryable=False` drops `retry_stage`: `Task.retry` guards FAILED ->
    PENDING, so a goal wedged by a SKIPPED or evidence-less DONE task cannot be
    retried and offering it yields a 422 on the operator's only obvious move.
    """
    resolutions = list(policy_for(kind).resolutions)
    if task_retryable is False and RETRY_STAGE in resolutions:
        resolutions.remove(RETRY_STAGE)
    return resolutions


def requires_human(kind: str) -> bool:
    return policy_for(kind).requires_human


__all__ = [
    "BIND_PROJECT",
    "BlockPolicy",
    "EDIT_TASK",
    "RETRY_STAGE",
    "START_REPLAN",
    "WAIT_AND_RETRY",
    "policy_for",
    "requires_human",
    "resolutions_for",
]
