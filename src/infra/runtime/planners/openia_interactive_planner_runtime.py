"""
src/infra/runtime/openai_interactive_runtime.py
"""

import json
import logging
from typing import Callable, Optional

from openai import OpenAI

from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)
from src.infra.runtime.planners.openai_planner_runtime import (
    _tool_to_openai_api,
    _serialize_tool_calls,
)

log = logging.getLogger(__name__)


class OpenAIInteractivePlannerRuntime(PlannerRuntimePort):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        io_handler: Optional[Callable[[str], str]] = None,
    ) -> None:
        # If base_url is provided (e.g. OpenRouter), the OpenAI SDK will route traffic there.

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model
        self._io_handler = io_handler or input

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:

        api_tools = [_tool_to_openai_api(t) for t in tools]
        tool_map = {t.name: t.handler for t in tools}

        messages = [
            {
                "role": "system",
                "content": "You are a requirements gatherer. Ask the user questions to build a project brief.",
            },
            {"role": "user", "content": prompt},
        ]

        brief_submitted = False
        final_text = ""

        for turn in range(max_turns):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=api_tools,
            )

            msg = response.choices[0].message
            final_text = msg.content or ""

            if session_callback:
                session_callback(
                    "assistant",
                    [{"text": final_text, "tool_calls": _serialize_tool_calls(msg.tool_calls)}],
                )

            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                raise PlannerRuntimeError(
                    "Interactive session ended without submitting project brief."
                )

            for tool_call in msg.tool_calls:
                handler = tool_map.get(tool_call.function.name)
                try:
                    args = json.loads(tool_call.function.arguments)

                    if tool_call.function.name == "ask_question":
                        question = args.get("question", "")
                        log.info("interactive_planner.ask_question", question=question)
                        answer = self._io_handler(question)
                        result_str = json.dumps({"answer": answer})
                    elif handler is None:
                        result_str = json.dumps(
                            {"error": f"Unknown tool: {tool_call.function.name}"}
                        )
                    else:
                        result_str = handler(args)
                except Exception as exc:
                    result_str = json.dumps({"error": str(exc)})

                if tool_call.function.name == "submit_project_brief":
                    try:
                        if json.loads(result_str).get("accepted"):
                            brief_submitted = True
                    except Exception:
                        pass

                tool_msg = {"role": "tool", "tool_call_id": tool_call.id, "content": result_str}
                messages.append(tool_msg)

                if session_callback:
                    session_callback("tool", [tool_msg])

            if brief_submitted:
                break

        if not brief_submitted:
            raise PlannerRuntimeError("Interactive planning session exceeded max turns")

        brief_raw = self._extract_brief_from_history(messages)

        return PlannerOutput(
            reasoning="",
            roadmap_raw=brief_raw,
            raw_text=final_text,
            turns=messages,
        )

    def _extract_brief_from_history(self, messages: list[dict]) -> dict:
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    if tc.get("function", {}).get("name") == "submit_project_brief":
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            return json.loads(args.get("brief_json", "{}"))
                        except Exception:
                            pass
        return {}
