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

from src.domain.value_objects.lifecycle import FailureKind
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


# How many times a session tells a model "that needed a tool call" before giving
# up. A lapse is common and cheap to correct; abandoning the session throws away
# every read turn already paid for. Bounded so a model that genuinely cannot call
# tools still surfaces instead of looping to the ceiling.
_MAX_PLAIN_REPLY_NUDGES = 2


def _payload_in_prose(text: str) -> str | None:
    """A JSON object the model wrote as TEXT instead of as a tool call.

    Observed live: a model read the repository, built a correct contract, and
    emitted it inside a ```json fence. The content was right and only the
    transport was wrong, so handing the payload back makes compliance trivial.

    This never accepts the payload as a submission — it only quotes it. Tool
    arguments are re-validated at the tool boundary by design, and letting prose
    in through a side door would bypass exactly that.
    """
    candidate = text.strip()
    if "```" in candidate:
        blocks = candidate.split("```")
        for block in blocks[1:]:
            body = block.split("\n", 1)[-1] if block[:20].strip().lower().startswith("json") else block
            body = body.strip()
            if body.startswith("{"):
                candidate = body
                break
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    snippet = candidate[start : end + 1]
    try:
        parsed = json.loads(snippet)
    except Exception:
        return None
    return snippet if isinstance(parsed, dict) else None


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
    reserved_submit_turns: int = 0,
    allow_plain_reply: bool = False,
) -> SessionResult:
    """Run the loop on ``messages`` (mutated in place: assistant turns and tool
    results are appended, so the caller sees the full transcript).

    ``reserved_submit_turns`` withholds the last N turns from READING. Every turn
    is one provider round-trip, so a profile with many read tools can spend the
    whole budget looking things up and never attempt a submission — observed live
    on goal enrichment, which died `exceeded 4 turns without submitting` having
    called only readers. Worse, a terminal handler's ``{"accepted": false}``
    self-correction assumes a turn remains for the repair.

    The reserve is enforced by WITHDRAWING the non-terminal tools from the request
    once the read budget is spent: the model cannot call what it is not offered, so
    the remaining turns are guaranteed to the terminal tool rather than merely
    counted after the fact. Zero (the default, used by conversation) keeps every
    tool available on every turn.
    """
    terminal_names = {t.name for t in tools if t.terminal}
    terminal_only = [tool for tool in tools if tool.terminal]
    nudges = 0
    read_budget = max(0, max_turns - reserved_submit_turns)
    read_turns = 0
    final_text = ""
    usage_total: dict[str, int] = {}

    for turn_index in range(max_turns):
        offered = tools if read_turns < read_budget else terminal_only
        turn = await client.complete(messages, offered)
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
            # Forgetting to call the tool is the cheapest failure there is to
            # correct, and abandoning the session throws away every read turn
            # already paid for. Nudge once — bounded, so a model that genuinely
            # cannot call tools still surfaces as a transient failure instead of
            # looping to the ceiling.
            if nudges < _MAX_PLAIN_REPLY_NUDGES:
                nudges += 1
                terminal_list = ", ".join(sorted(terminal_names))
                payload = _payload_in_prose(final_text)
                correction = (
                    f"You replied with text, but this step requires a tool call. "
                    f"Call {terminal_list} now with your best complete answer. "
                    "Do not reply with prose or with a JSON code block."
                )
                if payload is not None:
                    correction = (
                        "This step requires a tool call. You already produced the payload "
                        "but sent it as text. Call "
                        f"{terminal_list} now with exactly these arguments:\n{payload[:4000]}"
                    )
                messages.append({"role": "user", "content": correction})
                continue
            raise ReasonerError(
                "Reasoner replied with plain text where a tool submit was "
                f"required (after {turn_index + 1} turn(s), {nudges} nudge(s)): "
                f"{final_text[:200]}",
                transient=True,
                kind=FailureKind.TOOL_ERROR,
                turns_used=turn_index + 1,
            )

        if not any(call.name in terminal_names for call in turn.tool_calls):
            read_turns += 1

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
        f"Reasoner session exceeded {max_turns} turns without submitting "
        f"({read_turns} spent on reads, {max_turns - read_turns} on submissions)",
        transient=True,
        kind=FailureKind.TOOL_ERROR,
        turns_used=max_turns,
    )
