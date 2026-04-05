from __future__ import annotations

import json
from typing import Callable, Optional

from src.domain.ports.planner import PlannerOutput, PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.base_agent_runtime import PlannerModelAdapter
from src.infra.runtime.planners.runtime_types import NormalizedToolResult
from src.infra.runtime.planners.tooling.tool_execution import execute_tool_call


class BaseInteractivePlannerRuntime:
    """Shared interactive loop for discovery mode runtimes."""

    def __init__(
        self,
        adapter: PlannerModelAdapter,
        io_handler: Optional[Callable[[str], str]] = None,
        ask_tool_name: str = "ask_question",
        submit_tool_name: str = "submit_project_brief",
        artifact_arg: str = "brief_json",
    ) -> None:
        self._adapter = adapter
        self._io_handler = io_handler or input
        self._ask_tool_name = ask_tool_name
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

        brief_submitted = False
        final_text = ""
        reasoning = ""
        artifact: dict = {}

        for _ in range(max_turns):
            turn = self._adapter.send_turn(messages, provider_tools)
            final_text = turn.final_text or final_text
            reasoning = turn.reasoning or reasoning

            if session_callback:
                session_callback("assistant", turn.content_blocks)

            self._adapter.append_assistant_turn(messages, turn)

            if not turn.tool_calls:
                raise PlannerRuntimeError(
                    "Interactive planner session ended without submitting project brief. "
                    "The planner must call submit_project_brief."
                )

            tool_results: list[NormalizedToolResult] = []
            for tool_call in turn.tool_calls:
                if tool_call.name == self._ask_tool_name:
                    question = tool_call.arguments.get("question", "")
                    result_str = json.dumps({"answer": self._io_handler(question)})
                    result = NormalizedToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        result_str=result_str,
                    )
                else:
                    result = execute_tool_call(tools=tools, tool_call=tool_call)

                tool_results.append(result)

                if tool_call.name == self._submit_tool_name:
                    try:
                        parsed = json.loads(result.result_str)
                        if parsed.get("accepted"):
                            brief_submitted = True
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

            if brief_submitted:
                break

        if not brief_submitted:
            raise PlannerRuntimeError(
                f"Interactive planning session exceeded max turns ({max_turns}) without submitting project brief"
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
