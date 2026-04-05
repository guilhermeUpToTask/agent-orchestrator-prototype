"""
src/infra/logging/live_logger.py — Thread-safe live logger for concurrent agents.

Provides:
  - Real-time streaming of logs to terminal (no buffering)
  - Agent-scoped logging with consistent prefix format
  - Thread-safe operation for concurrent agents
  - Both JSON internal storage and readable terminal rendering
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TextIO

from .log_events import LogEvent, LogEventType


class LiveLogger:
    """
    Thread-safe logger that streams events in real-time.

    Features:
      - Real-time output to terminal (no buffering)
      - Concurrent-safe: each agent gets its own lock
      - Structured JSON storage alongside readable rendering
      - Configurable output streams for stdout/stderr
    """

    # Color codes for terminal output
    COLORS = {
        LogEventType.AGENT_START: "\033[32m",     # Green
        LogEventType.AGENT_END: "\033[32m",       # Green
        LogEventType.LLM_REQUEST: "\033[34m",     # Blue
        LogEventType.LLM_RESPONSE: "\033[34m",    # Blue
        LogEventType.TOOL_CALL_START: "\033[36m", # Cyan
        LogEventType.TOOL_CALL_END: "\033[36m",   # Cyan
        LogEventType.STDOUT: "\033[37m",          # White (bright)
        LogEventType.STDERR: "\033[31m",          # Red
        LogEventType.AGENT_ERROR: "\033[31m",     # Red
        LogEventType.AGENT_OUTPUT: "\033[33m",    # Yellow
        # Planning layer events
        LogEventType.PLANNER_SESSION_START: "\033[35m",  # Magenta
        LogEventType.PLANNER_TURN: "\033[35m",           # Magenta
        LogEventType.PLANNER_TOOL_CALL: "\033[36m",      # Cyan
        LogEventType.PLANNER_TOOL_RESULT: "\033[36m",    # Cyan
        LogEventType.PLANNER_SESSION_END: "\033[35m",    # Magenta
        LogEventType.PLANNER_DECISION: "\033[33m",       # Yellow
        LogEventType.PLANNER_PHASE: "\033[33m",          # Yellow
        LogEventType.JIT_PLAN_START: "\033[34m",         # Blue
        LogEventType.JIT_PLAN_END: "\033[34m",           # Blue
        LogEventType.GOAL_DISPATCHED: "\033[32m",        # Green
    }

    # Event type labels for readable output
    LABELS = {
        LogEventType.AGENT_START: "START",
        LogEventType.AGENT_END: "END",
        LogEventType.LLM_REQUEST: "LLM_REQ",
        LogEventType.LLM_RESPONSE: "LLM_RSP",
        LogEventType.TOOL_CALL_START: "TOOL_START",
        LogEventType.TOOL_CALL_END: "TOOL_END",
        LogEventType.STDOUT: "STDOUT",
        LogEventType.STDERR: "STDERR",
        LogEventType.AGENT_ERROR: "ERROR",
        LogEventType.AGENT_OUTPUT: "OUTPUT",
        # Planning layer labels
        LogEventType.PLANNER_SESSION_START: "PLAN_START",
        LogEventType.PLANNER_TURN: "PLAN_TURN",
        LogEventType.PLANNER_TOOL_CALL: "PLAN_TOOL",
        LogEventType.PLANNER_TOOL_RESULT: "PLAN_RSLT",
        LogEventType.PLANNER_SESSION_END: "PLAN_END",
        LogEventType.PLANNER_DECISION: "DECISION",
        LogEventType.PLANNER_PHASE: "PHASE",
        LogEventType.JIT_PLAN_START: "JIT_START",
        LogEventType.JIT_PLAN_END: "JIT_END",
        LogEventType.GOAL_DISPATCHED: "GOAL_DISP",
    }

    RESET = "\033[0m"

    def __init__(
        self,
        json_log_dir: Optional[Path] = None,
        stdout_stream: TextIO = sys.stdout,
        stderr_stream: TextIO = sys.stderr,
    ) -> None:
        """
        Initialize the live logger.

        Args:
            json_log_dir: Directory to store JSON log files (None = no file storage)
            stdout_stream: Stream for normal output (default: sys.stdout)
            stderr_stream: Stream for error output (default: sys.stderr)
        """
        self.json_log_dir = json_log_dir
        self.stdout_stream = stdout_stream
        self.stderr_stream = stderr_stream

        # Per-agent state: lock, JSON file handle, event buffer
        self._agent_state: dict[str, Any] = {}
        self._global_lock = threading.Lock()

        # Ensure JSON log directory exists
        if self.json_log_dir:
            self.json_log_dir.mkdir(parents=True, exist_ok=True)

    def register_agent(
        self,
        agent_name: str,
        session_id: str,
        workspace_path: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Register a new agent session for logging.

        Args:
            agent_name: Name of the agent (e.g., "pi", "gemini", "claude")
            session_id: Unique session identifier
            workspace_path: Path to the agent's workspace
            metadata: Additional metadata to store
        """
        with self._global_lock:
            if agent_name in self._agent_state:
                # Agent already registered - might be reused, skip
                return

            # Initialize per-agent state
            agent_data = {
                "session_id": session_id,
                "workspace": workspace_path,
                "lock": threading.Lock(),  # Per-agent lock for thread safety
                "json_file": None,
                "event_buffer": [],
                "start_time": time.monotonic(),
                "metadata": metadata or {},
            }

            # Open JSON log file if directory is configured
            if self.json_log_dir:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                json_path = self.json_log_dir / f"{agent_name}_{timestamp}_{session_id}.jsonl"
                agent_data["json_file"] = open(json_path, "w", encoding="utf-8")
                agent_data["json_path"] = json_path

            self._agent_state[agent_name] = agent_data

    def log_event(self, event: LogEvent) -> None:
        """
        Log an event for a specific agent.

        This is thread-safe: each agent has its own lock.

        Args:
            event: The log event to record
        """
        agent_name = event.agent_name
        if agent_name not in self._agent_state:
            # Agent not registered yet - register with basic info
            self.register_agent(agent_name, f"unknown-{int(time.time())}", "unknown")

        agent_state = self._agent_state[agent_name]
        lock = agent_state["lock"]

        with lock:
            # Set timestamp if not already set
            if event.timestamp == 0.0:
                event.timestamp = time.monotonic() - agent_state["start_time"]

            # Store in memory buffer (for get_json_logs)
            agent_state["event_buffer"].append(event.to_json())

            # Store in JSON file if configured
            if agent_state["json_file"]:
                json_line = json.dumps(event.to_json())
                agent_state["json_file"].write(json_line + "\n")
                agent_state["json_file"].flush()

            # Stream to terminal in real-time
            self._render_to_terminal(event)

    def _render_to_terminal(self, event: LogEvent) -> None:
        """Render a log event to the terminal in readable format."""
        # Format timestamp (human-readable)
        timestamp_str = f"[{event.timestamp:.2f}s]"

        # Get color and label for event type
        color = self.COLORS.get(event.event_type, "")
        label = self.LABELS.get(event.event_type, event.event_type.value)

        # Build the formatted line
        prefix = f"{color}[{timestamp_str}] [{event.agent_name}] [{label}]{self.RESET}"
        line = f"{prefix} {event.message}"

        # Determine output stream based on event type
        stream = self.stderr_stream if event.event_type in (
            LogEventType.STDERR, LogEventType.AGENT_ERROR
        ) else self.stdout_stream

        # Write to stream (use write + flush for real-time output)
        stream.write(line + "\n")
        stream.flush()

    def close_agent(self, agent_name: str) -> None:
        """
        Close logging for an agent session.

        Args:
            agent_name: Name of the agent to close
        """
        with self._global_lock:
            if agent_name not in self._agent_state:
                return

            agent_state = self._agent_state[agent_name]

            # Close JSON file if open
            if "json_file" in agent_state and agent_state["json_file"]:
                agent_state["json_file"].close()

            # Remove from active agents
            del self._agent_state[agent_name]

    def get_json_logs(self, agent_name: str) -> list[LogEvent]:
        """
        Retrieve all logged events for an agent as LogEvent objects.

        Args:
            agent_name: Name of the agent

        Returns:
            List of LogEvent objects
        """
        # Note: This loads from memory buffer. For complete logs,
        # the JSON file should be read separately.
        if agent_name not in self._agent_state:
            return []

        agent_state = self._agent_state[agent_name]
        with agent_state["lock"]:
            return [LogEvent.from_json(e) for e in agent_state["event_buffer"]]

    def get_json_log_path(self, agent_name: str) -> Optional[Path]:
        """
        Get the path to the JSON log file for an agent.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to JSON log file, or None if not configured
        """
        if agent_name not in self._agent_state:
            return None

        return self._agent_state[agent_name].get("json_path")

    def close(self) -> None:
        """Close all agent sessions and release resources."""
        with self._global_lock:
            agent_names = list(self._agent_state.keys())
            for agent_name in agent_names:
                self.close_agent(agent_name)


# Global logger instance (singleton pattern)
_global_logger: Optional[LiveLogger] = None
_logger_lock = threading.Lock()


def get_logger(json_log_dir: Optional[Path] = None) -> LiveLogger:
    """
    Get the global logger instance.

    Args:
        json_log_dir: Directory to store JSON logs (only used on first call)

    Returns:
        The global LiveLogger instance
    """
    global _global_logger

    with _logger_lock:
        if _global_logger is None:
            _global_logger = LiveLogger(json_log_dir=json_log_dir)
        return _global_logger


def reset_logger() -> None:
    """Reset the global logger (useful for testing)."""
    global _global_logger
    with _logger_lock:
        if _global_logger:
            _global_logger.close()
        _global_logger = None


def log_event(event: LogEvent) -> None:
    """Convenience function to log an event using the global logger."""
    logger = get_logger()
    logger.log_event(event)
