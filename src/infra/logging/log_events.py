"""
src/infra/logging/log_events.py — Structured log event definitions.

All internal logging events use structured JSON representation.
These are rendered to readable terminal format by LiveLogger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class LogEventType(str, Enum):
    """Event types for the observability system."""
    # Agent lifecycle events
    AGENT_START = "AGENT_START"
    AGENT_END = "AGENT_END"

    # LLM interaction events
    LLM_REQUEST = "LLM_REQUEST"
    LLM_RESPONSE = "LLM_RESPONSE"

    # Tool call events
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_END = "TOOL_CALL_END"

    # Output capture events
    STDOUT = "STDOUT"
    STDERR = "STDERR"

    # Error and result events
    AGENT_ERROR = "AGENT_ERROR"
    AGENT_OUTPUT = "AGENT_OUTPUT"

    # Planning layer events (Tier 1 Strategic + Tier 2 JIT)
    PLANNER_SESSION_START = "PLANNER_SESSION_START"
    PLANNER_TURN = "PLANNER_TURN"           # one LLM → human message received
    PLANNER_TOOL_CALL = "PLANNER_TOOL_CALL"  # tool invoked by the LLM
    PLANNER_TOOL_RESULT = "PLANNER_TOOL_RESULT"
    PLANNER_SESSION_END = "PLANNER_SESSION_END"
    PLANNER_DECISION = "PLANNER_DECISION"   # propose_decision tool fired
    PLANNER_PHASE = "PLANNER_PHASE"         # propose_phase_plan tool fired
    JIT_PLAN_START = "JIT_PLAN_START"
    JIT_PLAN_END = "JIT_PLAN_END"
    GOAL_DISPATCHED = "GOAL_DISPATCHED"


@dataclass
class LogEvent:
    """Structured internal representation of a log event (JSON-serializable)."""
    event_type: LogEventType
    agent_name: str
    timestamp: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "event_type": self.event_type.value,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LogEvent:
        """Create LogEvent from JSON dictionary."""
        return cls(
            event_type=LogEventType(data["event_type"]),
            agent_name=data["agent_name"],
            timestamp=data["timestamp"],
            message=data["message"],
            details=data.get("details", {}),
        )


# Convenience builders for common events
def build_agent_start_event(agent_name: str, session_id: str, workspace: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.AGENT_START,
        agent_name=agent_name,
        timestamp=0.0,  # Will be set by logger
        message=f"Agent {agent_name} started",
        details={
            "session_id": session_id,
            "workspace": workspace,
        },
    )


def build_agent_end_event(agent_name: str, session_id: str, exit_code: int, elapsed: float) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.AGENT_END,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"Agent {agent_name} ended with exit code {exit_code}",
        details={
            "session_id": session_id,
            "exit_code": exit_code,
            "elapsed_seconds": elapsed,
        },
    )


def build_llm_request_event(agent_name: str, model: str, prompt_preview: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.LLM_REQUEST,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"LLM request to {model}",
        details={
            "model": model,
            "prompt_preview": prompt_preview[:200] + "..." if len(prompt_preview) > 200 else prompt_preview,
        },
    )


def build_llm_response_event(agent_name: str, model: str, response_preview: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.LLM_RESPONSE,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"LLM response from {model}",
        details={
            "model": model,
            "response_preview": response_preview[:200] + "..." if len(response_preview) > 200 else response_preview,
        },
    )


def build_tool_call_start_event(agent_name: str, tool_name: str, arguments: dict[str, Any]) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.TOOL_CALL_START,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"Tool call: {tool_name}",
        details={
            "tool_name": tool_name,
            "arguments": arguments,
        },
    )


def build_tool_call_end_event(agent_name: str, tool_name: str, result_preview: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.TOOL_CALL_END,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"Tool result: {tool_name}",
        details={
            "tool_name": tool_name,
            "result_preview": result_preview[:200] + "..." if len(result_preview) > 200 else result_preview,
        },
    )


def build_stdout_event(agent_name: str, line: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.STDOUT,
        agent_name=agent_name,
        timestamp=0.0,
        message=line.rstrip(),
        details={},
    )


def build_stderr_event(agent_name: str, line: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.STDERR,
        agent_name=agent_name,
        timestamp=0.0,
        message=line.rstrip(),
        details={},
    )


def build_agent_error_event(agent_name: str, error: str, context: Optional[str] = None) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.AGENT_ERROR,
        agent_name=agent_name,
        timestamp=0.0,
        message=f"Agent error: {error}",
        details={
            "error": error,
            "context": context,
        },
    )


def build_agent_output_event(agent_name: str, output: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.AGENT_OUTPUT,
        agent_name=agent_name,
        timestamp=0.0,
        message="Agent output captured",
        details={
            "output_preview": output[:200] + "..." if len(output) > 200 else output,
        },
    )


# ---------------------------------------------------------------------------
# Planning layer convenience builders
# ---------------------------------------------------------------------------

def build_planner_session_start_event(mode: str, session_id: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.PLANNER_SESSION_START,
        agent_name="planner",
        timestamp=0.0,
        message=f"Planning session started: mode={mode} session={session_id}",
        details={"mode": mode, "session_id": session_id},
    )


def build_planner_turn_event(role: str, preview: str, turn_index: int) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.PLANNER_TURN,
        agent_name="planner",
        timestamp=0.0,
        message=f"Turn {turn_index} [{role}]: {preview}",
        details={"role": role, "preview": preview, "turn_index": turn_index},
    )


def build_planner_tool_call_event(tool_name: str, args_preview: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.PLANNER_TOOL_CALL,
        agent_name="planner",
        timestamp=0.0,
        message=f"Tool call: {tool_name}",
        details={"tool_name": tool_name, "args_preview": args_preview},
    )


def build_planner_tool_result_event(tool_name: str, accepted: bool, preview: str) -> LogEvent:
    status = "accepted" if accepted else "rejected"
    return LogEvent(
        event_type=LogEventType.PLANNER_TOOL_RESULT,
        agent_name="planner",
        timestamp=0.0,
        message=f"Tool result: {tool_name} → {status}",
        details={"tool_name": tool_name, "accepted": accepted, "preview": preview},
    )


def build_planner_session_end_event(
    mode: str, success: bool, elapsed_s: float, turn_count: int
) -> LogEvent:
    status = "success" if success else "failed"
    return LogEvent(
        event_type=LogEventType.PLANNER_SESSION_END,
        agent_name="planner",
        timestamp=0.0,
        message=f"Planning session ended: {status} after {turn_count} turns ({elapsed_s:.1f}s)",
        details={
            "mode": mode,
            "success": success,
            "elapsed_s": elapsed_s,
            "turn_count": turn_count,
        },
    )


def build_planner_decision_event(decision_id: str, domain: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.PLANNER_DECISION,
        agent_name="planner",
        timestamp=0.0,
        message=f"Decision proposed: [{decision_id}] domain={domain}",
        details={"decision_id": decision_id, "domain": domain},
    )


def build_planner_phase_event(phase_name: str, goal_names: list) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.PLANNER_PHASE,
        agent_name="planner",
        timestamp=0.0,
        message=f"Phase proposed: {phase_name} ({len(goal_names)} goals)",
        details={"phase_name": phase_name, "goal_names": goal_names},
    )


def build_jit_plan_start_event(goal_id: str, goal_name: str) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.JIT_PLAN_START,
        agent_name="planner",
        timestamp=0.0,
        message=f"JIT planning started for goal: {goal_name}",
        details={"goal_id": goal_id, "goal_name": goal_name},
    )


def build_jit_plan_end_event(goal_id: str, task_ids: list, elapsed_s: float) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.JIT_PLAN_END,
        agent_name="planner",
        timestamp=0.0,
        message=f"JIT planning complete: {len(task_ids)} tasks in {elapsed_s:.1f}s",
        details={"goal_id": goal_id, "task_ids": task_ids, "elapsed_s": elapsed_s},
    )


def build_goal_dispatched_event(goal_id: str, goal_name: str, phase_index: int) -> LogEvent:
    return LogEvent(
        event_type=LogEventType.GOAL_DISPATCHED,
        agent_name="planner",
        timestamp=0.0,
        message=f"Goal dispatched: {goal_name} (phase {phase_index})",
        details={"goal_id": goal_id, "goal_name": goal_name, "phase_index": phase_index},
    )
