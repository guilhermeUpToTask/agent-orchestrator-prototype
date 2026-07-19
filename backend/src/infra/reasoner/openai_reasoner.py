"""
src/infra/reasoner/openai_reasoner.py — the real Reasoner (OpenAI-compatible).

Implements the purpose-specific domain port on the runtime package's agent loop:

  converse    — system + persisted history replayed as PLAIN user/assistant
                text (never provider transcripts: immune to dangling tool
                calls and provider switches) + the phase prompt. One terminal
                tool: submit_intent_proposal. A plain-text reply keeps discovery
                waiting; a valid submit opens the exact-revision intent gate.
  architect_cycle — submit_cycle_draft with stable keys and dependencies.
  enrich_goal_contract — submit_goal_contract for the head goal only.
  enrich_goal — quarantined compatibility tool for legacy plans.

Handlers RE-VALIDATE everything (provider schema enforcement is never
trusted) and build the domain objects with new_id() and position=index.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

import structlog

from src.app.observations import (
    ModelUsagePayload,
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationRepository,
    ObservationSource,
    TelemetryObservation,
)
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.execution_contracts import (
    GoalContract,
    VerificationStrategy,
)
from src.domain.entities.planning_artifacts import GoalOutline
from src.domain.entities.task import Task
from src.domain.factories.identity import new_id
from src.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    IntentCandidate,
    ReasonerReply,
)
from src.infra.reasoner.runtime.agent_loop import SessionResult, run_tool_session
from src.infra.reasoner.runtime.llm_client import LLMClient
from src.infra.reasoner.runtime.prompts import (
    SYSTEM_PROMPT,
    build_discovery_prompt,
    build_enrich_prompt,
    build_replanning_prompt,
)
from src.infra.reasoner.runtime.tools import ToolSpec
from src.infra.reasoner.runtime.tool_profiles import (
    ArtifactCollector,
    ReasoningPurpose,
    build_tool_profile,
)

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

SUBMIT_TASKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tasks": {"type": "array", "minItems": 1, "items": _TASK_ITEM_SCHEMA}},
    "required": ["tasks"],
}

SUBMIT_CYCLE_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "position": {"type": "integer", "minimum": 0},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["key", "name", "objective", "position", "depends_on"],
            },
        }
    },
    "required": ["goals"],
}

SUBMIT_INTENT_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "normalized_brief": {"type": "string"},
        "objective": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "normalized_brief",
        "objective",
        "scope",
        "constraints",
        "exclusions",
        "assumptions",
        "unresolved_questions",
    ],
}

_CRITERION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["id", "description"],
}

SUBMIT_GOAL_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "acceptance_criteria": {
            "type": "array",
            "minItems": 1,
            "items": _CRITERION_SCHEMA,
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "acceptance_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": _CRITERION_SCHEMA,
                    },
                    "goal_criterion_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "allowed_scope": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "forbidden_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "verification_commands": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "verification_strategy": {
                        "type": "string",
                        "enum": [item.value for item in VerificationStrategy],
                    },
                    "required_capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "objective",
                    "acceptance_criteria",
                    "goal_criterion_ids",
                    "allowed_scope",
                    "verification_commands",
                    "verification_strategy",
                ],
            },
        },
        "cross_task_integration_criterion_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["objective", "acceptance_criteria", "tasks"],
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
        observation_repository: ObservationRepository | None = None,
        provider: str | None = None,
    ) -> None:
        self._client = client
        self._default_caps = list(capabilities or [])
        self._converse_max_turns = converse_max_turns
        self._enrich_max_turns = enrich_max_turns
        self._observation_repository = observation_repository
        self._provider = provider

    async def _emit_usage(self, plan: Plan, mode: str, result: SessionResult) -> None:
        """Persist provider usage without fabricating absent token counts.

        Observation failure is isolated from planning: passive telemetry can be
        lost, but it cannot change a domain transition or reasoner result.
        """
        if self._observation_repository is None:
            return
        input_tokens = result.usage.get("prompt_tokens")
        output_tokens = result.usage.get("completion_tokens")
        reasoning_tokens = result.usage.get("reasoning_tokens")
        cached_tokens = result.usage.get("cached_tokens")
        total_tokens = result.usage.get("total_tokens")
        reported = any(
            value is not None
            for value in (
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cached_tokens,
                total_tokens,
            )
        )
        observation = TelemetryObservation(
            correlation=ObservationCorrelation(plan_id=plan.id),
            observed_at=datetime.now(timezone.utc),
            source=ObservationSource.PROVIDER,
            quality=(ObservationQuality.REPORTED if reported else ObservationQuality.UNAVAILABLE),
            kind=ObservationKind.MODEL_USAGE,
            payload=ModelUsagePayload(
                model_request_count=result.llm_calls,
                turn_count=result.turns,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=cached_tokens,
                total_tokens=total_tokens,
                model=getattr(self._client, "model", None),
                provider=self._provider,
                context=mode,
                phase=plan.phase.value,
                unavailable_reason=(None if reported else "provider_did_not_report_usage"),
            ),
        )
        try:
            await self._observation_repository.append(observation)
        except Exception:
            log.warning(
                "reasoner.usage_observation_failed",
                observation_id=observation.observation_id,
                exc_info=True,
            )

    # ---- converse -------------------------------------------------------
    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply:
        prompt = (
            build_discovery_prompt(plan) if mode == "discovery" else build_replanning_prompt(plan)
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

        collector = ArtifactCollector()
        readers = {
            "read_project_spec": lambda: json.dumps({"project_id": plan.project_id}),
            "read_project_plan": lambda: plan.model_dump_json(),
            "read_repository_context": lambda: json.dumps({"availability": "adapter_context_only"}),
            "read_conversation": lambda: json.dumps(
                [
                    {"role": item.role, "content": item.content}
                    for item in list(history)[-MAX_HISTORY_MESSAGES:]
                ]
            ),
        }
        tools = build_tool_profile(
            ReasoningPurpose.INTENT_DISCOVERY,
            readers,
            SUBMIT_INTENT_PROPOSAL_SCHEMA,
            collector.submit,
        )

        result = await run_tool_session(
            self._client,
            messages,
            tools,
            max_turns=self._converse_max_turns,
            allow_plain_reply=True,
        )
        await self._emit_usage(plan, mode, result)

        if not result.submitted:
            return ReasonerReply(
                message=result.text,
                model_request_count=result.llm_calls,
                tool_turn_count=result.turns,
            )

        candidate = IntentCandidate.model_validate(collector.value or result.submit_args)
        if candidate.unresolved_questions:
            raise ValueError("submitted intent cannot retain unresolved questions")
        reply_text = result.text or "Intent proposal is ready for your review."
        return ReasonerReply(
            message=reply_text,
            intent=candidate,
            model_request_count=result.llm_calls,
            tool_turn_count=result.turns,
        )

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
                errors.extend(_validate_task_item(task_raw, f"tasks[{ti}]", known_caps))
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
                f"Submit the ordered task breakdown for goal '{goal.name}'. Call exactly once."
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
        await self._emit_usage(plan, "enrich", result)
        return [
            _build_task(task_raw, ti, known_caps)
            for ti, task_raw in enumerate(result.submit_args["tasks"])
            if isinstance(task_raw, dict)
        ]

    async def architect_cycle(self, plan: Plan) -> list[GoalOutline]:
        proposal = plan.intent_proposal
        if proposal is None or proposal.approved_at is None:
            raise ValueError("approved intent is required for cycle architecture")
        collector = ArtifactCollector()
        readers = {
            "read_project_spec": lambda: json.dumps({"project_id": plan.project_id}),
            "read_project_plan": lambda: plan.model_dump_json(),
            "read_repository_context": lambda: json.dumps({"availability": "adapter_context_only"}),
            "read_approved_intent": lambda: proposal.model_dump_json(),
            "read_prior_evidence": lambda: json.dumps(
                [cycle.evidence_refs for cycle in plan.cycles]
            ),
        }
        tools = build_tool_profile(
            ReasoningPurpose.CYCLE_ARCHITECTURE,
            readers,
            SUBMIT_CYCLE_DRAFT_SCHEMA,
            collector.submit,
        )
        source_instruction = (
            " This is a replan. Read the project plan and prior evidence before "
            "submitting. Treat DONE goals and tasks as locked history: do not "
            "recreate or redo them. Account only for unfinished source work and "
            "the newly approved intent in the replacement cycle."
            if proposal.source_cycle_id is not None
            else ""
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Generate one ordered CycleDraft for the approved intent. "
                    "Use stable local goal keys and only real dependency keys; "
                    "strict execution order is represented by position, not fake edges. "
                    f"{source_instruction} Submit it with submit_cycle_draft."
                ),
            },
        ]
        result = await run_tool_session(
            self._client,
            messages,
            tools,
            max_turns=self._converse_max_turns,
            allow_plain_reply=False,
        )
        await self._emit_usage(plan, "cycle_architecture", result)
        value = collector.value or result.submit_args
        goals = [GoalOutline.model_validate(item) for item in value.get("goals", [])]
        # Reuse CycleDraft's validator at the application boundary; the adapter
        # returns only candidate DTOs and cannot activate anything.
        return goals

    async def enrich_goal_contract(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> GoalContract:
        proposal = next(
            (cycle for cycle in plan.cycles if cycle.status.value == "active"),
            None,
        )
        collector = ArtifactCollector()
        readers = {
            "read_project_spec": lambda: json.dumps({"project_id": plan.project_id}),
            "read_project_plan": lambda: plan.model_dump_json(),
            "read_repository_context": lambda: json.dumps({"availability": "adapter_context_only"}),
            "read_approved_intent": lambda: json.dumps(
                {"intent_proposal_id": proposal.intent_proposal_id if proposal else None}
            ),
            "read_active_goal": lambda: goal.model_dump_json(),
            "read_prior_evidence": lambda: json.dumps(proposal.evidence_refs if proposal else []),
        }
        tools = build_tool_profile(
            ReasoningPurpose.GOAL_ENRICHMENT,
            readers,
            SUBMIT_GOAL_CONTRACT_SCHEMA,
            collector.submit,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Freeze a complete GoalContract for the active goal. Every goal "
                    "criterion must map to atomic ordered tasks. Use TDD for new "
                    "behavior, characterization for preserving behavior, and "
                    "executable_check where RED is meaningless. Submit exactly once."
                ),
            },
        ]
        result = await run_tool_session(
            self._client,
            messages,
            tools,
            max_turns=self._enrich_max_turns,
            allow_plain_reply=False,
        )
        await self._emit_usage(plan, "goal_enrichment", result)
        value = dict(collector.value or result.submit_args)
        value["id"] = goal.id
        value["frozen_at"] = datetime.min.replace(tzinfo=timezone.utc)
        for position, task in enumerate(value.get("tasks", [])):
            task["id"] = new_id()
            task["position"] = position
            task["revision"] = 1
        return GoalContract.model_validate(value)


def _only_capability_errors(errors: list[str]) -> bool:
    return all("unknown capability id" in e for e in errors)
