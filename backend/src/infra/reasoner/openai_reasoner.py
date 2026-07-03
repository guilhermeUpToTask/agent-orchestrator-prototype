"""
src/infra/reasoner/openai_reasoner.py — the real Reasoner (OpenAI-compatible).

Implements the two-method domain port on the runtime package's agent loop:

  converse    — system + persisted history replayed as PLAIN user/assistant
                text (never provider transcripts: immune to dangling tool
                calls and provider switches) + the phase prompt. One terminal
                tool: submit_goals. A plain-text reply IS the question turn
                (goals=None); the submit is the roadmap commit.
  enrich_goal — one terminal tool (submit_tasks), plain replies disallowed,
                short budget. Unknown capability ids come back as
                {accepted:false, errors} so the model self-corrects; after
                repeated rejections the final submit is accepted with unknown
                ids filtered out (logged) rather than failing the session.

Handlers RE-VALIDATE everything (provider schema enforcement is never
trusted) and build the domain objects with new_id() and position=index.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

import structlog

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
from src.infra.reasoner.runtime.agent_loop import run_tool_session
from src.infra.reasoner.runtime.llm_client import LLMClient
from src.infra.reasoner.runtime.prompts import (
    SYSTEM_PROMPT,
    build_discovery_prompt,
    build_enrich_prompt,
    build_replanning_prompt,
)
from src.infra.reasoner.runtime.tools import ToolSpec

log = structlog.get_logger(__name__)

MAX_HISTORY_MESSAGES = 30  # context-growth cap: replay only the recent tail

# How many unknown-capability rejections a session absorbs before the submit
# is accepted with the unknown ids FILTERED (logged) instead of rejected —
# a stubborn model must not burn the whole turn budget on one bad id.
MAX_CAPABILITY_REJECTIONS = 2

_TASK_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "imperative task name"},
        "description": {
            "type": "string",
            "description": "precise, executable-without-questions description",
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "capability ids from the catalog (optional)",
        },
    },
    "required": ["name", "description"],
}

SUBMIT_GOALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": _TASK_ITEM_SCHEMA,
                        "description": "optional pre-populated tasks",
                    },
                },
                "required": ["name", "description"],
            },
        }
    },
    "required": ["goals"],
}

SUBMIT_TASKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {"type": "array", "minItems": 1, "items": _TASK_ITEM_SCHEMA}
    },
    "required": ["tasks"],
}


def _rejected(errors: list[str]) -> str:
    return json.dumps({"accepted": False, "errors": errors})


def _accepted() -> str:
    return json.dumps({"accepted": True})


def _validate_task_item(item: Any, where: str, known_caps: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(item, dict):
        return [f"{where}: each task must be an object"]
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        errors.append(f"{where}: task 'name' must be a non-empty string")
    if not isinstance(item.get("description"), str):
        errors.append(f"{where}: task 'description' must be a string")
    caps = item.get("required_capabilities", [])
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        errors.append(f"{where}: 'required_capabilities' must be a list of strings")
    else:
        unknown = [c for c in caps if c not in known_caps]
        if unknown:
            errors.append(
                f"{where}: unknown capability id(s) {unknown} — use only ids "
                "from the catalog (or omit required_capabilities)"
            )
    return errors


def _build_task(item: dict[str, Any], position: int, known_caps: set[str]) -> Task:
    caps_raw = item.get("required_capabilities", [])
    caps = [c for c in caps_raw if isinstance(c, str)] if isinstance(caps_raw, list) else []
    kept = [c for c in caps if c in known_caps]
    if kept != caps:
        log.warning(
            "reasoner.unknown_capabilities_filtered",
            task=item.get("name"),
            dropped=[c for c in caps if c not in known_caps],
        )
    return Task(
        id=new_id(),
        name=str(item["name"]).strip(),
        position=position,
        description=str(item.get("description", "")),
        required_capabilities=kept,
    )


class OpenAIReasoner:
    def __init__(
        self,
        client: LLMClient,
        capabilities: Sequence[Capability] | None = None,
        *,
        converse_max_turns: int = 8,
        enrich_max_turns: int = 4,
    ) -> None:
        self._client = client
        self._default_caps = list(capabilities or [])
        self._converse_max_turns = converse_max_turns
        self._enrich_max_turns = enrich_max_turns

    # ---- converse -------------------------------------------------------
    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply:
        prompt = (
            build_discovery_prompt(plan)
            if mode == "discovery"
            else build_replanning_prompt(plan)
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        # replay persisted history as plain text turns (never provider
        # transcripts) — provider-agnostic and immune to dangling tool calls
        for msg in list(history)[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message or "(proceed)"})

        known_caps = {c.id for c in self._default_caps}
        state: dict[str, Any] = {"rejections": 0}

        def handle_submit_goals(args: dict[str, Any]) -> str:
            errors: list[str] = []
            goals_raw = args.get("goals")
            if not isinstance(goals_raw, list) or not goals_raw:
                return _rejected(["'goals' must be a non-empty array"])
            for gi, goal_raw in enumerate(goals_raw):
                where = f"goals[{gi}]"
                if not isinstance(goal_raw, dict):
                    errors.append(f"{where}: must be an object")
                    continue
                if not isinstance(goal_raw.get("name"), str) or not goal_raw["name"].strip():
                    errors.append(f"{where}: 'name' must be a non-empty string")
                if not isinstance(goal_raw.get("description"), str):
                    errors.append(f"{where}: 'description' must be a string")
                tasks_raw = goal_raw.get("tasks", [])
                if not isinstance(tasks_raw, list):
                    errors.append(f"{where}: 'tasks' must be an array when present")
                    continue
                for ti, task_raw in enumerate(tasks_raw):
                    errors.extend(
                        _validate_task_item(
                            task_raw, f"{where}.tasks[{ti}]", known_caps
                        )
                    )
            if errors and _only_capability_errors(errors):
                state["rejections"] += 1
                if state["rejections"] <= MAX_CAPABILITY_REJECTIONS:
                    return _rejected(errors)
                return _accepted()  # final: accept, filter unknown ids on build
            if errors:
                return _rejected(errors)
            return _accepted()

        submit_goals = ToolSpec(
            name="submit_goals",
            description=(
                "Commit the agreed goal roadmap (ordered). Call exactly once, "
                "when the direction is clear."
            ),
            input_schema=SUBMIT_GOALS_SCHEMA,
            handler=handle_submit_goals,
            terminal=True,
        )

        result = await run_tool_session(
            self._client,
            messages,
            [submit_goals],
            max_turns=self._converse_max_turns,
            allow_plain_reply=True,
        )

        if not result.submitted:
            return ReasonerReply(message=result.text, goals=None)

        goals = self._build_goals(result.submit_args, known_caps)
        reply_text = result.text or (
            f"Committing {len(goals)} goal(s) to the roadmap."
        )
        return ReasonerReply(message=reply_text, goals=goals)

    def _build_goals(
        self, args: dict[str, Any], known_caps: set[str]
    ) -> list[Goal]:
        goals: list[Goal] = []
        for gi, goal_raw in enumerate(args["goals"]):
            tasks = [
                _build_task(task_raw, ti, known_caps)
                for ti, task_raw in enumerate(goal_raw.get("tasks", []))
                if isinstance(task_raw, dict)
            ]
            goals.append(
                Goal(
                    id=new_id(),
                    name=str(goal_raw["name"]).strip(),
                    position=gi,
                    description=str(goal_raw.get("description", "")),
                    tasks=tasks,
                )
            )
        return goals

    # ---- enrich_goal ----------------------------------------------------
    async def enrich_goal(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> list[Task]:
        caps = list(capabilities)
        known_caps = {c.id for c in caps}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_enrich_prompt(plan, goal, caps)},
        ]
        state: dict[str, Any] = {"rejections": 0}

        def handle_submit_tasks(args: dict[str, Any]) -> str:
            tasks_raw = args.get("tasks")
            if not isinstance(tasks_raw, list) or not tasks_raw:
                return _rejected(["'tasks' must be a non-empty array"])
            errors: list[str] = []
            for ti, task_raw in enumerate(tasks_raw):
                errors.extend(
                    _validate_task_item(task_raw, f"tasks[{ti}]", known_caps)
                )
            if errors and _only_capability_errors(errors):
                state["rejections"] += 1
                if state["rejections"] <= MAX_CAPABILITY_REJECTIONS:
                    return _rejected(errors)
                return _accepted()  # final: accept, filter unknown ids on build
            if errors:
                return _rejected(errors)
            return _accepted()

        submit_tasks = ToolSpec(
            name="submit_tasks",
            description=(
                f"Submit the ordered task breakdown for goal '{goal.name}'. "
                "Call exactly once."
            ),
            input_schema=SUBMIT_TASKS_SCHEMA,
            handler=handle_submit_tasks,
            terminal=True,
        )

        result = await run_tool_session(
            self._client,
            messages,
            [submit_tasks],
            max_turns=self._enrich_max_turns,
            allow_plain_reply=False,
        )
        return [
            _build_task(task_raw, ti, known_caps)
            for ti, task_raw in enumerate(result.submit_args["tasks"])
            if isinstance(task_raw, dict)
        ]


def _only_capability_errors(errors: list[str]) -> bool:
    return all("unknown capability id" in e for e in errors)
