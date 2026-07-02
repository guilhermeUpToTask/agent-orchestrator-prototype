"""
src/infra/logging — Live logging and observability for agent orchestration.

This module provides non-intrusive live logging capabilities:
  - Real-time streaming of agent output
  - Thread-safe concurrent logging
  - Structured JSON events with readable terminal rendering
  - Zero changes to existing runtime implementations

Usage:
    from src.infra.logging import LiveLogger, get_logger
"""
from __future__ import annotations

# Re-export main classes
from .live_logger import LiveLogger, get_logger, reset_logger, log_event
from .log_events import (
    LogEvent,
    LogEventType,
    build_agent_start_event,
    build_agent_end_event,
    build_llm_request_event,
    build_llm_response_event,
    build_tool_call_start_event,
    build_tool_call_end_event,
    build_stdout_event,
    build_stderr_event,
    build_agent_error_event,
    build_agent_output_event,
)
__all__ = [
    # Logger
    "LiveLogger",
    "get_logger",
    "reset_logger",
    "log_event",
    # Events
    "LogEvent",
    "LogEventType",
    "build_agent_start_event",
    "build_agent_end_event",
    "build_llm_request_event",
    "build_llm_response_event",
    "build_tool_call_start_event",
    "build_tool_call_end_event",
    "build_stdout_event",
    "build_stderr_event",
    "build_agent_error_event",
    "build_agent_output_event",
    # Wrapper
]
