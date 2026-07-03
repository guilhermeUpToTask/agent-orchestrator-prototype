"""
src/infra/reasoner/stub_reasoner.py — deterministic Reasoner (no LLM).

Drives the whole 9-phase machine end-to-end for tests, dry-run demos and the
pre-made-plan flows. The REAL reasoner (OpenAI-compatible) replaces this behind
the same port; nothing else changes.

converse() grammar (one directive per line, anything else ignored):

    ask: what stack do you prefer?   -> reply WITHOUT goals (multi-turn hook:
                                        the phase stays put, chat continues)
    goal: Build the API
    task: scaffold FastAPI app [caps: backend,python]
    task: add health endpoint
    goal: Ship it                    -> goals present = the roadmap commit

`[caps: a,b]` on a task line sets required_capabilities (capability IDS).
In discovery mode the plan brief is parsed together with the message (a plan
created with a grammar brief commits on the first, even empty, message); a
text with no `goal:` lines falls back to one goal with one task so any
free-text input still produces a runnable plan. Goals may be committed
WITHOUT tasks — the ENRICHING JIT step populates those.

enrich_goal() returns one deterministic task per goal:
    implement: <goal name>   (description "[enriched] ...")
"""
from __future__ import annotations

import re
from typing import Sequence

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.factories.identity import new_id
from src.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    ReasonerReply,
)

_CAPS_RE = re.compile(r"\[caps:\s*([^\]]+)\]")
_ASK_RE = re.compile(r"^ask:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_goals(text: str, fallback_name: str) -> list[Goal]:
    goals: list[Goal] = []
    saw_goal_line = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("goal:"):
            saw_goal_line = True
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
    if not saw_goal_line:
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


class StubReasoner:
    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply:
        ask = _ASK_RE.search(message)
        if ask:
            # deterministic multi-turn hook: echo the question, commit nothing
            return ReasonerReply(message=ask.group(1).strip(), goals=None)

        if mode == "discovery":
            source = f"{plan.brief}\n{message}".strip()
            fallback = "deliver the brief"
        else:
            source = message
            fallback = f"iteration-{plan.iteration + 1} re-plan"
        goals = _parse_goals(source, fallback_name=fallback)
        return ReasonerReply(
            message=f"Committing {len(goals)} goal(s) to the roadmap.",
            goals=goals,
        )

    async def enrich_goal(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> list[Task]:
        return [
            Task(
                id=new_id(),
                name=f"implement: {goal.name}",
                position=0,
                description=f"[enriched] implement: {goal.name} (goal: {goal.name})",
            )
        ]
