"""
src/infra/runtime/interactive_planner_runtime.py — Interactive planner runtime.

This runtime is for DISCOVERY mode. Unlike AnthropicPlannerRuntime which
loops autonomously, this one pauses and yields to the caller after each
ask_question tool call, waits for human input, then continues.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-opus-4-6"


class AnthropicInteractivePlannerRuntime(PlannerRuntimePort):
    """
    Agentic loop that pauses for human input after ask_question tool calls.

    Used exclusively in DISCOVERY mode where the planner needs back-and-forth
    conversation before it can produce a ProjectBrief.

    The loop exits when the planner calls submit_project_brief.
    Human answers are injected via the answer_queue or collected from stdin
    depending on the io_handler provided.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        thinking_budget: int = 8000,
        io_handler: Optional[Callable[[str], str]] = None,
    ) -> None:
        """
        Args:
            api_key: Anthropic API key
            model: Model name (default: claude-opus-4-6)
            thinking_budget: Thinking budget tokens (default: 8000)
            io_handler: Function that takes a question string and returns an answer.
                       Defaults to input() if None.
        """
        self._api_key = api_key
        self._model = model
        self._thinking_budget = thinking_budget
        self._io_handler = io_handler or input

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:
        """
        Run the interactive planning loop.

        Unlike the autonomous runtime, this one:
        1. Sends messages to API
        2. On ask_question tool call: pauses, calls io_handler, continues
        3. On submit_project_brief: exits loop and returns
        4. Raises PlannerRuntimeError if max_turns exceeded or end_turn without tool calls
        """
        try:
            import anthropic
        except ImportError as exc:
            raise PlannerRuntimeError(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        api_tools = [_tool_to_api(t) for t in tools]
        tool_map = {t.name: t.handler for t in tools}

        messages: list[dict] = [{"role": "user", "content": prompt}]
        brief_submitted = False
        final_text = ""
        reasoning = ""
        turns_used = 0

        for turn in range(max_turns):
            turns_used = turn + 1
            response = client.messages.create(
                model=self._model,
                max_tokens=16000,
                thinking={"type": "enabled", "budget_tokens": self._thinking_budget},
                tools=api_tools,
                messages=messages,
            )

            # Extract reasoning from thinking blocks
            for block in response.content:
                if block.type == "thinking":
                    reasoning = getattr(block, "thinking", "")
                elif block.type == "text":
                    final_text = getattr(block, "text", "")

            # Serialize content for persistence
            content_blocks = _serialize_content(response.content)
            if session_callback:
                session_callback("assistant", content_blocks)

            # Append assistant turn to history
            messages.append({"role": "assistant", "content": response.content})

            # Process tool calls
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            # Check for end_turn without tool calls
            if not tool_use_blocks:
                # No tool calls — this is NOT valid for interactive mode
                # Must call submit_project_brief to exit
                raise PlannerRuntimeError(
                    "Interactive planner session ended without submitting project brief. "
                    "The planner must call submit_project_brief."
                )

            tool_results: list[dict] = []
            for block in tool_use_blocks:
                handler = tool_map.get(block.name)
                if handler is None:
                    result_str = json.dumps({"error": f"Unknown tool: {block.name}"})
                elif block.name == "ask_question":
                    # Pause and get human input
                    question = block.input.get("question", "")
                    log.info("interactive_planner.ask_question", question=question)
                    answer = self._io_handler(question)
                    result_str = json.dumps({"answer": answer})
                else:
                    try:
                        result_str = handler(block.input)
                    except Exception as exc:
                        result_str = json.dumps({"error": str(exc)})

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    }
                )

                # Check if submit_project_brief was called → break loop
                if block.name == "submit_project_brief":
                    try:
                        parsed = json.loads(result_str)
                        if parsed.get("accepted"):
                            brief_submitted = True
                    except Exception:
                        pass

            # Persist tool results turn
            if session_callback:
                session_callback("tool_result", tool_results)

            messages.append({"role": "user", "content": tool_results})

            if brief_submitted:
                break

        if not brief_submitted:
            raise PlannerRuntimeError(
                f"Interactive planning session exceeded max turns ({max_turns}) "
                "without submitting project brief"
            )

        # Extract brief data from submit_project_brief result in message history
        brief_raw = _extract_brief_from_history(messages)

        return PlannerOutput(
            reasoning=reasoning,
            roadmap_raw=brief_raw,  # Store brief as roadmap_raw for compatibility
            raw_text=final_text,
            turns=messages,
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

        # Simulate: assistant calls submit_project_brief
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

        # Execute the tool
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_to_api(tool: PlannerTool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _serialize_content(content_blocks: list) -> list[dict]:
    """Convert Anthropic SDK content blocks to plain dicts for storage."""
    result = []
    for block in content_blocks:
        if hasattr(block, "model_dump"):
            result.append(block.model_dump())
        elif hasattr(block, "__dict__"):
            result.append({k: v for k, v in vars(block).items() if not k.startswith("_")})
        else:
            result.append({"type": "unknown", "raw": str(block)})
    return result


def _extract_brief_from_history(messages: list[dict]) -> dict:
    """Find the brief_raw from the submit_project_brief tool call in history."""
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            # Check tool_use blocks in assistant messages
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            bname = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if btype == "tool_use" and bname == "submit_project_brief":
                binput = (
                    block.get("input") if isinstance(block, dict) else getattr(block, "input", {})
                )
                raw = binput.get("brief_json", "") if isinstance(binput, dict) else ""
                try:
                    return json.loads(raw)
                except Exception:
                    pass
    return {}
