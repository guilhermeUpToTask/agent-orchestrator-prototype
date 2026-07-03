"""
The reasoner prompts — system prompt + per-phase instruction blocks, ported
from the old planning_prompt_builders and adapted to the two-method port:
converse (discovery / replanning) and enrich_goal. There is no structure
prompt: ARCHITECTURE is a no-LLM passthrough (see PlanningHandler).
"""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.goal import Goal
from src.infra.reasoner.runtime.context import (
    render_capabilities,
    render_plan_context,
)
from src.domain.entities.capability import Capability
from typing import Sequence

SYSTEM_PROMPT = (
    "You are an expert software architect and planner working inside an agent "
    "orchestrator. You plan; you never execute. Use the provided tools to "
    "submit structured plans. Work efficiently: you have a limited number of "
    "tool-turns."
)


def build_discovery_prompt(plan: Plan) -> str:
    return (
        f"{render_plan_context(plan)}\n\n"
        "---\n\n"
        "## Discovery conversation\n\n"
        "You are in the DISCOVERY phase: agree a goal roadmap with the user "
        "through conversation, then commit it.\n\n"
        "1. If requirements are unclear, reply in plain text with your "
        "questions or proposal — the user answers in the next message. Keep "
        "it short and concrete.\n"
        "2. When the direction is clear (or the user asks you to proceed), "
        "call `submit_goals` with an ordered list of 1-6 goals. Each goal "
        "needs a name and a one-paragraph description. You MAY pre-populate a "
        "goal's tasks when they are obvious; goals submitted without tasks "
        "are broken into tasks later, one goal at a time.\n"
        "3. If `submit_goals` returns `{accepted: false, errors: [...]}`, fix "
        "exactly those problems and resubmit — do not re-litigate the roadmap."
    )


def build_replanning_prompt(plan: Plan) -> str:
    return (
        f"{render_plan_context(plan, include_results=True)}\n\n"
        "---\n\n"
        "## Re-planning conversation\n\n"
        f"You are in the REPLANNING phase (iteration {plan.iteration} is "
        "history; you are planning the next one). The context above shows "
        "what was actually built — completed goals are history and must NOT "
        "be re-planned or redone.\n\n"
        "1. Discuss the next iteration with the user in plain text if their "
        "intent is unclear.\n"
        "2. When the direction is clear, call `submit_goals` with the NEW "
        "goals only (1-6, ordered). They will be appended after the history.\n"
        "3. On `{accepted: false, errors: [...]}`, fix exactly those problems "
        "and resubmit."
    )


def build_enrich_prompt(
    plan: Plan, goal: Goal, capabilities: Sequence[Capability]
) -> str:
    goal_desc = goal.description or "no further description"
    return (
        f"{render_plan_context(plan)}\n\n"
        f"{render_capabilities(capabilities)}\n\n"
        "---\n\n"
        "## Task breakdown request\n\n"
        f"Break the goal **{goal.name}** ({goal_desc}) "
        "into a SMALL ordered list of plain executable tasks "
        "(1-6; prefer fewer). Each task is one unit of work a coding agent "
        "completes in a single session: give it an imperative name and a "
        "description precise enough to execute without asking questions. Set "
        "`required_capabilities` ONLY from the catalog ids above (or leave "
        "it empty).\n\n"
        "Call `submit_tasks` exactly once. If it returns "
        "`{accepted: false, errors: [...]}`, fix exactly those problems and "
        "resubmit. Do not plan tasks for any other goal."
    )
