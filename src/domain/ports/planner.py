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
    handler: Callable[[dict], str]          # takes tool input, returns string result


@dataclass(frozen=True)
class PlannerOutput:
    """Structured result returned by PlannerRuntimePort.run_session()."""
    reasoning: str
    roadmap_raw: dict[str, Any]             # validated JSON from submit_final_roadmap
    raw_text: str                           # full final assistant message
    decisions_update: str = ""
    arch_update: str = ""
    turns: list[dict] = field(default_factory=list)  # raw message history


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
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:
        """
        Run the agentic planning loop and return a PlannerOutput.

        session_callback(role, content_blocks) is called after each turn so
        the PlannerSession can persist turns in real time.

        Raises PlannerRuntimeError if max_turns is exceeded without calling
        submit_final_roadmap, or on unrecoverable API errors.
        """
        ...
