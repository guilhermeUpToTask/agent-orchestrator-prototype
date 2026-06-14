"""
src/domain/ports/planner.py — PlannerRuntimePort and associated types.

The planning runtime is an agentic loop, not a single LLM call.  The port
runs a multi-turn conversation where the model can call tools between
thinking steps.  The app layer defines the tools as callbacks; the runtime
manages message history, thinking blocks, and the turn loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class PlannerTool:
    """A tool the planner agent can invoke during its agentic loop."""
    name: str
    description: str
    input_schema: dict[str, Any]            # JSON Schema
    handler: Callable[[dict[str, Any]], str]  # takes tool input, returns string result
    terminal: bool = False                  # calling this tool (with accepted=True) ends the session


@dataclass(frozen=True)
class PlannerOutput:
    """Structured result returned by PlannerRuntimePort.run_session()."""
    reasoning: str
    roadmap_raw: dict[str, Any]             # validated JSON from the terminal/submit tool
    raw_text: str                           # full final assistant message
    decisions_update: str = ""
    arch_update: str = ""
    turns: list[dict[str, Any]] = field(default_factory=list)  # raw message history
    submitted: bool = False                 # did the agent explicitly call a terminal tool


class PlannerRuntimeError(Exception):
    """Raised when the planner runtime cannot produce a valid roadmap."""


class PlannerRuntimePort(ABC):
    """
    Contract for running an agentic planning session.

    The runtime manages the multi-turn conversation loop, tool dispatch,
    and turn persistence callbacks.  The app layer provides the tools and
    the session_callback; the runtime owns the message history and API calls.
    """

    @abstractmethod
    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict[str, Any]]], None]] = None,
        require_submit: bool = True,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> PlannerOutput:
        """
        Run the agentic planning loop and return a PlannerOutput.

        session_callback(role, content_blocks) is called after each turn so
        the PlannerSession can persist turns in real time.

        A tool flagged ``terminal=True`` ends the session when it accepts.
        ``PlannerOutput.submitted`` reports whether the agent reached such a
        terminal tool before the loop stopped.

        cancel_check, if provided, is polled between turns; returning True
        stops the loop cooperatively (the caller decides how to finalize).

        If require_submit is True, the runtime raises PlannerRuntimeError when
        the loop ends without a terminal submit (or on unrecoverable API
        errors). If False, it returns the partial PlannerOutput instead and
        leaves finalize-or-fail to the caller.
        """
        ...
