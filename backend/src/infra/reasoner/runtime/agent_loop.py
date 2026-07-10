"""
The tool-calling agent loop — the old BasePlannerRuntime, ported.

run_tool_session drives a multi-turn conversation: the model thinks, calls
tools, sees their results, and eventually either calls a TERMINAL tool whose
handler accepts (``{"accepted": true}``) — the submit — or, when
``allow_plain_reply`` is set (the conversational phases), answers in plain
text, which ends the session as a reply-without-submit (the question turn).

Self-correction is the handler's contract: a terminal handler that returns
``{"accepted": false, "errors": [...]}`` keeps the session open and the model
sees exactly what to fix. Budget exhaustion without a submit (or a plain-text
stop where a submit was required) raises a TRANSIENT ReasonerError — the
worker logs it, backs off, and the lease lets any worker retry from persisted
state.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.infra.reasoner.runtime.errors import ReasonerError
from src.infra.reasoner.runtime.llm_client import LLMClient
from src.infra.reasoner.runtime.tools import ToolResult, ToolSpec, execute_tool_call


class SessionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str  # the last assistant display text
    submitted: bool  # did a terminal tool accept?
    submit_args: dict[str, Any]  # the accepted terminal call's arguments
    turns: int  # assistant turns consumed
    llm_calls: int = 0  # provider calls made (== turns on a clean run)
    usage: dict[str, int] = {}  # summed token usage across the session's turns


def _accumulate_usage(total: dict[str, int], turn_usage: dict[str, int] | None) -> None:
    if not turn_usage:
        return
    for key, value in turn_usage.items():
        total[key] = total.get(key, 0) + value


async def run_tool_session(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec],
    *,
    max_turns: int = 8,
    allow_plain_reply: bool = False,
) -> SessionResult:
    """Run the loop on ``messages`` (mutated in place: assistant turns and tool
    results are appended, so the caller sees the full transcript)."""
    terminal_names = {t.name for t in tools if t.terminal}
    final_text = ""
    usage_total: dict[str, int] = {}

    for turn_index in range(max_turns):
        turn = await client.complete(messages, tools)
        final_text = turn.text or final_text
        _accumulate_usage(usage_total, turn.usage)
        messages.append(turn.raw_message)

        if not turn.tool_calls:
            if allow_plain_reply:
                # conversational: the plain text IS the reply (question turn)
                return SessionResult(
                    text=final_text,
                    submitted=False,
                    submit_args={},
                    turns=turn_index + 1,
                    llm_calls=turn_index + 1,
                    usage=usage_total,
                )
            raise ReasonerError(
                "Reasoner replied with plain text where a tool submit was "
                f"required (after {turn_index + 1} turn(s)): {final_text[:200]}",
                transient=True,
            )

        submitted_args: dict[str, Any] | None = None
        results: list[ToolResult] = []
        for tool_call in turn.tool_calls:
            result = execute_tool_call(tools, tool_call)
            results.append(result)
            if tool_call.name in terminal_names and submitted_args is None:
                try:
                    parsed = json.loads(result.result_str)
                except Exception:
                    parsed = {}
                if isinstance(parsed, dict) and parsed.get("accepted"):
                    submitted_args = tool_call.arguments

        for result in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.result_str,
                }
            )

        if submitted_args is not None:
            return SessionResult(
                text=final_text,
                submitted=True,
                submit_args=submitted_args,
                turns=turn_index + 1,
                llm_calls=turn_index + 1,
                usage=usage_total,
            )

    raise ReasonerError(
        f"Reasoner session exceeded {max_turns} turns without submitting",
        transient=True,
    )
