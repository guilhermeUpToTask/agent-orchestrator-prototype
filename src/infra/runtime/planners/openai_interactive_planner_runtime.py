"""
src/infra/runtime/openai_interactive_runtime.py
"""

from typing import Callable, Optional

from src.domain.ports.planner import PlannerOutput, PlannerRuntimePort, PlannerTool
from src.infra.runtime.planners.adapters.openai_adapter import OpenAIPlannerAdapter
from src.infra.runtime.planners.base_interactive_runtime import BaseInteractivePlannerRuntime


class OpenAIInteractivePlannerRuntime(PlannerRuntimePort):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        io_handler: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._runtime = BaseInteractivePlannerRuntime(
            adapter=OpenAIPlannerAdapter(
                api_key=api_key,
                model=model,
                base_url=base_url,
                system_prompt=(
                    "You are a requirements gatherer. "
                    "Ask the user questions to build a project brief."
                ),
                temperature=0.2,
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
