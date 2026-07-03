"""
src/infra/reasoner/stub_reasoner.py — deterministic Reasoner (no LLM).

Drives the whole 9-phase machine end-to-end for tests, dry-run demos and the
pre-made-plan flows. The REAL reasoner (OpenAI, roadmap 2.5) replaces this
behind the same port; nothing else changes.

Brief / message grammar (one directive per line, anything else ignored):

    goal: Build the API
    task: scaffold FastAPI app [caps: backend,python]
    task: add health endpoint
    goal: Ship it
    task: write Dockerfile

`[caps: a,b]` on a task line sets required_capabilities (capability IDS).
A brief with no `goal:` lines falls back to one goal with one task so any
free-text brief still produces a runnable plan.

Phase transforms:
    draft_goals      — parse the brief with the grammar above.
    structure_goals  — identity pass over the current iteration's goals
                       (ordering is already the parse order).
    enrich_goals     — fills empty task descriptions (the "detail" step).
    replan_goals     — parse the chat message with the same grammar; falls back
                       to one goal derived from the message.
"""
from __future__ import annotations

import re

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.factories.identity import new_id

_CAPS_RE = re.compile(r"\[caps:\s*([^\]]+)\]")


def _parse_goals(text: str, fallback_name: str) -> list[Goal]:
    goals: list[Goal] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("goal:"):
            name = line[5:].strip()
            goals.append(
                Goal(
                    id=new_id(),
                    name=name,
                    position=len(goals),
                    description=name,
                    tasks=[],
                )
            )
        elif line.lower().startswith("task:") and goals:
            body = line[5:].strip()
            caps: list[str] = []
            caps_match = _CAPS_RE.search(body)
            if caps_match:
                caps = [c.strip() for c in caps_match.group(1).split(",") if c.strip()]
                body = _CAPS_RE.sub("", body).strip()
            goal = goals[-1]
            goal.tasks.append(
                Task(
                    id=new_id(),
                    name=body,
                    position=len(goal.tasks),
                    description="",
                    required_capabilities=caps,
                )
            )
    if not goals:
        goals = [
            Goal(
                id=new_id(),
                name=fallback_name,
                position=0,
                description=fallback_name,
                tasks=[
                    Task(
                        id=new_id(),
                        name=f"do: {text.strip()[:60] or fallback_name}",
                        position=0,
                        description="",
                    )
                ],
            )
        ]
    return goals


def _current_iteration_goals(plan: Plan) -> list[Goal]:
    return [g.model_copy(deep=True) for g in plan.goals if not g.is_terminal]


class StubReasoner:
    async def draft_goals(self, brief: str) -> list[Goal]:
        return _parse_goals(brief, fallback_name="deliver the brief")

    async def structure_goals(self, plan: Plan) -> list[Goal]:
        return _current_iteration_goals(plan)

    async def enrich_goals(self, plan: Plan) -> list[Goal]:
        goals = _current_iteration_goals(plan)
        for goal in goals:
            for task in goal.tasks:
                if not task.description:
                    task.description = f"[enriched] {task.name} (goal: {goal.name})"
        return goals

    async def replan_goals(self, plan: Plan, message: str) -> list[Goal]:
        return _parse_goals(
            message, fallback_name=f"iteration-{plan.iteration + 1} re-plan"
        )
