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

TDD_TASK_GRANULARITY_GUIDANCE = (
    "For any task whose `verification_strategy` is `tdd`, make the task a "
    "feature-level deliverable slice: its objective must state what is being "
    "delivered and validated (for example, 'Deliver validated Item schemas "
    "with passing tests'). Never submit a write-tests-only or make-tests-pass-"
    "only task. The runtime performs the red/green split internally for each "
    "TDD task: its test-author stage writes the failing tests and freezes the "
    "bundle, then its implementer stage makes those tests pass."
)


def build_discovery_prompt(plan: Plan) -> str:
    return (
        f"{render_plan_context(plan)}\n\n"
        "---\n\n"
        "## Discovery conversation\n\n"
        "You are discovering intent, not generating a roadmap or tasks.\n\n"
        "1. Normalize the submitted brief and make every safe assumption you can.\n"
        "2. If material questions remain, reply in plain text using exactly these "
        "headings: 'Normalized brief', 'Safe assumptions', 'Unresolved questions', "
        "and finish with 'Waiting for your answers.' Ask only unresolved questions.\n"
        "3. When the intent is clear, call `submit_intent_proposal`. Do not submit "
        "goals or tasks; roadmap architecture happens only after human approval."
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
        "1. Normalize the requested next-cycle intent and preserve completed work.\n"
        "2. If material questions remain, reply with only the unresolved questions "
        "after stating the normalized brief and safe assumptions. Finish with "
        "'Waiting for your answers.'\n"
        "3. When clear, call `submit_intent_proposal`. Roadmap goals are generated "
        "only after the exact proposal revision is approved."
    )


def build_enrich_prompt(plan: Plan, goal: Goal, capabilities: Sequence[Capability]) -> str:
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
        f"{TDD_TASK_GRANULARITY_GUIDANCE}\n\n"
        "Call `submit_tasks` exactly once. If it returns "
        "`{accepted: false, errors: [...]}`, fix exactly those problems and "
        "resubmit. Do not plan tasks for any other goal."
    )
