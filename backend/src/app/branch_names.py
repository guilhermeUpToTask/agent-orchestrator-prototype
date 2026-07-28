"""The git branch ladder, in one place.

`infra/git/workspace.py` built these names in two places — `begin` and
`_merge_goal_sync` — and CLAUDE.md described a third, different shape. That is
how the documented ladder drifted from the one the adapter actually creates:
the local variable holding the goal branch in `begin` is still named
`plan_branch`. Recording promoted refs adds a fourth reader (the execution
handler), so the convention gets one definition instead of a fourth copy.

`src/app` is the only layer that can host it. The dependency rule is
`domain -> app -> infra & api`, so infra may import app but never the reverse,
and the domain is frozen.
"""

from __future__ import annotations


def cycle_branch(cycle_id: str) -> str:
    """Cut from the repository's detected default branch on first use."""
    return f"cycle/{cycle_id}"


def goal_branch(goal_id: str) -> str:
    """Cut from the cycle branch; merged back into it on promotion."""
    return f"goal/{goal_id}"


def task_branch(task_id: str, run_id: str) -> str:
    """One branch per RUN. A run id is globally unique, so a retry never reuses
    the branch a previous attempt left behind."""
    return f"task/{task_id}/{run_id}"


def legacy_plan_branch(plan_id: str) -> str:
    """Pre-cyclic plans only. A cyclic plan has no plan-level branch."""
    return f"plan/{plan_id}"


def legacy_task_branch(task_id: str, attempt: int) -> str:
    """Pre-cyclic plans only."""
    return f"task/{task_id}/a{attempt}"
