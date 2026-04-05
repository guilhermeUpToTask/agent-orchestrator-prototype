from __future__ import annotations

import json
from typing import Callable, Optional, Protocol

from src.domain.ports.planner import PlannerOutput, PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.runtime_types import (
    NormalizedAssistantTurn,
    NormalizedToolCall,
    NormalizedToolResult,
)
from src.infra.runtime.planners.tooling.tool_execution import execute_tool_call


class PlannerModelAdapter(Protocol):
    def initial_messages(self, prompt: str) -> list[dict]:
        ...

    def to_provider_tools(self, tools: list[PlannerTool]) -> list[dict]:
        ...

    def send_turn(self, messages: list[dict], provider_tools: list[dict]) -> NormalizedAssistantTurn:
        ...

    def append_assistant_turn(self, messages: list[dict], turn: NormalizedAssistantTurn) -> None:
        ...

    def append_tool_results(self, messages: list[dict], results: list[NormalizedToolResult]) -> None:
        ...

    def extract_artifact(self, messages: list[dict], submit_tool_name: str, artifact_arg: str) -> dict:
        ...


class BasePlannerRuntime:
    """Provider-agnostic agent loop for planner runtimes."""

    def __init__(
        self,
        adapter: PlannerModelAdapter,
        submit_tool_name: str = "submit_final_roadmap",
        artifact_arg: str = "roadmap_json",
    ) -> None:
        self._adapter = adapter
        self._submit_tool_name = submit_tool_name
        self._artifact_arg = artifact_arg

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:
        provider_tools = self._adapter.to_provider_tools(tools)
        messages = self._adapter.initial_messages(prompt)

        final_text = ""
        reasoning = ""
        submitted = False
        artifact: dict = {}

        for _ in range(max_turns):
            turn = self._adapter.send_turn(messages, provider_tools)
            final_text = turn.final_text or final_text
            reasoning = turn.reasoning or reasoning

            if session_callback:
                session_callback("assistant", turn.content_blocks)

            self._adapter.append_assistant_turn(messages, turn)

            if not turn.tool_calls:
                break

            tool_results: list[NormalizedToolResult] = []
            for tool_call in turn.tool_calls:
                result = execute_tool_call(tools=tools, tool_call=tool_call)
                tool_results.append(result)
                if tool_call.name == self._submit_tool_name:
                    try:
                        parsed = json.loads(result.result_str)
                        if parsed.get("accepted"):
                            submitted = True
                            artifact = _coerce_artifact(tool_call.arguments.get(self._artifact_arg))
                    except Exception:
                        pass

            if session_callback:
                session_callback(
                    "tool_result",
                    [
                        {
                            "tool_use_id": r.tool_call_id,
                            "name": r.tool_name,
                            "content": r.result_str,
                        }
                        for r in tool_results
                    ],
                )

            self._adapter.append_tool_results(messages, tool_results)

            if submitted:
                break

        if not submitted:
            raise PlannerRuntimeError(
                "Planning session exceeded max turns without submitting a roadmap"
            )

        if not artifact:
            artifact = self._adapter.extract_artifact(
                messages,
                submit_tool_name=self._submit_tool_name,
                artifact_arg=self._artifact_arg,
            )

        return PlannerOutput(
            reasoning=reasoning,
            roadmap_raw=artifact,
            raw_text=final_text,
            turns=messages,
        )


def _coerce_artifact(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}
