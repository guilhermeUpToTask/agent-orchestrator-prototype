"""
src/infra/runtime/interactive_planner_runtime.py — Interactive planner runtime.

This runtime is for DISCOVERY mode. Unlike AnthropicPlannerRuntime which
loops autonomously, this one pauses and yields to the caller after each
ask_question tool call, waits for human input, then continues.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)
from src.infra.runtime.planners.adapters.anthropic_adapter import AnthropicPlannerAdapter
from src.infra.runtime.planners.base_interactive_runtime import BaseInteractivePlannerRuntime

_DEFAULT_MODEL = "claude-opus-4-6"


class AnthropicInteractivePlannerRuntime(PlannerRuntimePort):
    """Anthropic interactive runtime backed by shared interactive loop."""

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        thinking_budget: int = 8000,
        io_handler: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._runtime = BaseInteractivePlannerRuntime(
            adapter=AnthropicPlannerAdapter(
                api_key=api_key,
                model=model,
                thinking_budget=thinking_budget,
            ),
            io_handler=io_handler,
            ask_tool_name="ask_question",
            submit_tool_name="submit_project_brief",
            artifact_arg="brief_json",
        )

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:
        return self._runtime.run_session(
            prompt=prompt,
            tools=tools,
            max_turns=max_turns,
            session_callback=session_callback,
        )


class StubInteractivePlannerRuntime(AnthropicInteractivePlannerRuntime):
    """
    For tests. Immediately calls submit_project_brief with a minimal valid
    brief. Does not call ask_question.
    """

    _STUB_BRIEF = {
        "vision": "Stub project vision for testing",
        "constraints": ["no real constraints in stub"],
        "phase_1_exit_criteria": "stub phase 1 done",
        "open_questions": [],
    }

    def __init__(self, custom_brief: Optional[dict] = None) -> None:
        self._custom_brief = custom_brief or self._STUB_BRIEF

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:
        tool_map = {t.name: t.handler for t in tools}
        brief_json = json.dumps(self._custom_brief)

        assistant_blocks = [
            {
                "type": "tool_use",
                "id": "stub-tool-1",
                "name": "submit_project_brief",
                "input": {"brief_json": brief_json},
            }
        ]
        if session_callback:
            session_callback("assistant", assistant_blocks)

        handler = tool_map.get("submit_project_brief")
        result_str = (
            handler({"brief_json": brief_json}) if handler else json.dumps({"accepted": True})
        )

        try:
            parsed = json.loads(result_str)
            if not parsed.get("accepted"):
                raise PlannerRuntimeError(
                    f"StubInteractivePlannerRuntime: submit_project_brief rejected: "
                    f"{parsed.get('error', 'unknown error')}"
                )
        except json.JSONDecodeError:
            raise PlannerRuntimeError(
                "StubInteractivePlannerRuntime: invalid JSON from submit_project_brief handler"
            )

        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": "stub-tool-1",
                "content": result_str,
            }
        ]
        if session_callback:
            session_callback("tool_result", tool_result_blocks)

        return PlannerOutput(
            reasoning="Stub reasoning: discovery complete.",
            roadmap_raw=self._custom_brief,
            raw_text="Stub output: project brief submitted.",
            turns=[],
        )
