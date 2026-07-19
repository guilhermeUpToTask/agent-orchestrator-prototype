"""
Plan -> markdown context for the reasoner prompts (the old
PlanningContextRenderer, ported to the new domain).

The reasoner never receives raw aggregates over the wire — it reads this
sanitized, truncated rendering. Rules that keep the context bounded:

  * terminal goals (prior iterations' history) render as one-liners;
  * live goals render fully — tasks, statuses, capability ids, agents;
  * DONE task outputs (the REPLANNING context: what was actually built) are
    included only when ``include_results`` and truncated to
    ``max_result_chars``.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task

DEFAULT_MAX_RESULT_CHARS = 800


def render_plan_context(
    plan: Plan,
    *,
    include_results: bool = False,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> str:
    sections: list[str] = [
        "## Plan",
        f"**Brief**: {plan.brief}",
        f"**Phase**: {plan.phase.value} — **Iteration**: {plan.iteration}",
    ]

    goal_sets: list[tuple[str | None, list[Goal]]] = (
        [
            (
                f"Cycle {cycle.id} [{cycle.status.value}]"
                + (" — active source" if plan.active_cycle is cycle else ""),
                cycle.goals,
            )
            for cycle in plan.cycles
        ]
        if plan.cycles
        else [(None, plan.goals)]
    )

    for cycle_heading, goals in goal_sets:
        if cycle_heading is not None:
            sections.append(f"\n## {cycle_heading}")
        terminal = [goal for goal in goals if goal.is_terminal]
        live = [goal for goal in goals if not goal.is_terminal]

        if terminal:
            sections.append("\n### Completed / closed goals (history — do not redo)")
            for goal in sorted(terminal, key=lambda item: item.position):
                sections.append(_render_terminal_goal(goal))
                if include_results:
                    sections.extend(_render_task_results(goal.tasks, max_result_chars))

        if live:
            sections.append("\n### Current / unfinished goals")
            for goal in sorted(live, key=lambda item: item.position):
                sections.append(_render_live_goal(goal))

    return "\n".join(sections)


def render_capabilities(capabilities: Sequence[Capability]) -> str:
    """The capability catalog as markdown — the ONLY ids task
    required_capabilities may reference."""
    if not capabilities:
        return "## Capability catalog\n(empty — leave required_capabilities empty)"
    lines = ["## Capability catalog (use these ids only)"]
    for cap in capabilities:
        desc = f" — {cap.description}" if cap.description else ""
        lines.append(f"- `{cap.id}`: {cap.name}{desc}")
    return "\n".join(lines)


def _render_terminal_goal(goal: Goal) -> str:
    done = sum(1 for t in goal.tasks if t.status.value == "done")
    return f"- [{goal.status.value}] **{goal.name}** — {done}/{len(goal.tasks)} tasks done"


def _render_live_goal(goal: Goal) -> str:
    lines = [f"### {goal.name} [{goal.status.value}]"]
    if goal.description and goal.description != goal.name:
        lines.append(goal.description)
    if not goal.tasks:
        lines.append("(no tasks yet)")
    for task in sorted(goal.tasks, key=lambda t: t.position):
        caps = (
            f" (caps: {', '.join(task.required_capabilities)})"
            if task.required_capabilities
            else ""
        )
        agent = f" [agent: {task.agent_id}]" if task.agent_id else ""
        lines.append(f"- [{task.status.value}] {task.name}{caps}{agent}")
    return "\n".join(lines)


def _render_task_results(tasks: list[Task], max_result_chars: int) -> list[str]:
    lines: list[str] = []
    for task in sorted(tasks, key=lambda t: t.position):
        if task.result is None or not task.result.output:
            continue
        output = task.result.output
        if len(output) > max_result_chars:
            output = output[:max_result_chars] + " …[truncated]"
        lines.append(f"  - `{task.name}` result: {output}")
    return lines
