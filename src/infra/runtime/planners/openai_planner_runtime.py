"""
src/infra/runtime/openai_planner_runtime.py — OpenAI-spec Planner runtime.

Works with any OpenAI-compatible endpoint (OpenAI, vLLM, LM Studio, OpenRouter).
"""

from typing import Callable, Optional

from src.domain.ports.planner import PlannerOutput, PlannerRuntimePort, PlannerTool
from src.infra.runtime.planners.adapters.openai_adapter import OpenAIPlannerAdapter
from src.infra.runtime.planners.base_agent_runtime import BasePlannerRuntime


class OpenAIPlannerRuntime(PlannerRuntimePort):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
    ) -> None:
        self._model = model
        self._runtime = BasePlannerRuntime(
            adapter=OpenAIPlannerAdapter(
                api_key=api_key,
                model=model,
                base_url=base_url,
                system_prompt=(
                    "You are an expert software architect and planner. "
                    "Use the provided tools to construct a roadmap."
                ),
                temperature=0.2,
            ),
            submit_tool_name="submit_final_roadmap",
            artifact_arg="roadmap_json",
        )

    @property
    def model(self) -> str:
        """Surfaced so telemetry wrappers report the real model, not 'unknown'."""
        return self._model

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
        require_submit: bool = True,
        cancel_check: Optional[Callable[[], bool]] = None,
        prior_turns: Optional[list[dict]] = None,
    ) -> PlannerOutput:
        return self._runtime.run_session(
            prompt=prompt,
            tools=tools,
            max_turns=max_turns,
            session_callback=session_callback,
            require_submit=require_submit,
            cancel_check=cancel_check,
            prior_turns=prior_turns,
        )
