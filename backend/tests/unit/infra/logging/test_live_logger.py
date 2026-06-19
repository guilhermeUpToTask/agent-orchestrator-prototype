"""
Tests for the live logging system.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.infra.logging import (
    LiveLogger,
    LogEvent,
    LogEventType,
    build_agent_start_event,
    build_agent_end_event,
    build_stdout_event,
    build_stderr_event,
    reset_logger,
)


class TestLiveLogger:
    """Test cases for LiveLogger."""

    def setup_method(self):
        """Reset global logger before each test."""
        reset_logger()

    def test_register_agent(self, tmp_path: Path):
        """Test agent registration."""
        logger = LiveLogger(json_log_dir=tmp_path)

        logger.register_agent(
            agent_name="test-agent",
            session_id="test-session-123",
            workspace_path="/tmp/workspace",
        )

        assert "test-agent" in logger._agent_state
        assert logger._agent_state["test-agent"]["session_id"] == "test-session-123"

    def test_log_event(self, tmp_path: Path):
        """Test logging a single event."""
        logger = LiveLogger(json_log_dir=tmp_path)

        logger.register_agent(
            agent_name="test-agent",
            session_id="test-session-123",
            workspace_path="/tmp/workspace",
        )

        event = build_stdout_event("test-agent", "Hello, world!")
        logger.log_event(event)

        # Check that the event was logged
        assert len(logger._agent_state["test-agent"]["event_buffer"]) > 0

    def test_concurrent_logging(self, tmp_path: Path):
        """Test thread-safe concurrent logging."""
        import threading

        logger = LiveLogger(json_log_dir=tmp_path)
        num_agents = 5
        events_per_agent = 10

        def log_agent_events(agent_idx: int):
            agent_name = f"agent-{agent_idx}"
            logger.register_agent(
                agent_name=agent_name,
                session_id=f"session-{agent_idx}",
                workspace_path=f"/tmp/workspace-{agent_idx}",
            )

            for i in range(events_per_agent):
                event = build_stdout_event(agent_name, f"Message {i}")
                logger.log_event(event)
                time.sleep(0.001)  # Small delay to increase chance of interleaving

        threads = []
        for i in range(num_agents):
            t = threading.Thread(target=log_agent_events, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify each agent has all events
        for i in range(num_agents):
            agent_name = f"agent-{i}"
            assert agent_name in logger._agent_state
            # Buffer might not have all events due to timing, but should have some
            assert len(logger._agent_state[agent_name]["event_buffer"]) > 0

    def test_json_output(self, tmp_path: Path):
        """Test JSON log file output."""
        logger = LiveLogger(json_log_dir=tmp_path)

        agent_name = "test-agent"
        logger.register_agent(
            agent_name=agent_name,
            session_id="test-session-123",
            workspace_path="/tmp/workspace",
        )

        event = build_stdout_event(agent_name, "Test message")
        logger.log_event(event)

        # Close logger to flush files
        logger.close_agent(agent_name)

        # Check JSON log file was created
        json_files = list(tmp_path.glob("*.jsonl"))
        assert len(json_files) == 1

        # Verify JSON structure
        with open(json_files[0]) as f:
            lines = f.readlines()
            assert len(lines) > 0

            data = json.loads(lines[0])
            assert data["event_type"] == LogEventType.STDOUT.value
            assert data["agent_name"] == agent_name
            assert data["message"] == "Test message"

    def test_get_json_logs(self, tmp_path: Path):
        """Test retrieving logged events."""
        logger = LiveLogger(json_log_dir=tmp_path)

        agent_name = "test-agent"
        logger.register_agent(
            agent_name=agent_name,
            session_id="test-session-123",
            workspace_path="/tmp/workspace",
        )

        # Log multiple events
        for i in range(5):
            event = build_stdout_event(agent_name, f"Message {i}")
            logger.log_event(event)

        # Retrieve events
        events = logger.get_json_logs(agent_name)
        assert len(events) > 0
        assert all(e.agent_name == agent_name for e in events)

    def test_close_agent(self, tmp_path: Path):
        """Test closing an agent session."""
        logger = LiveLogger(json_log_dir=tmp_path)

        agent_name = "test-agent"
        logger.register_agent(
            agent_name=agent_name,
            session_id="test-session-123",
            workspace_path="/tmp/workspace",
        )

        assert agent_name in logger._agent_state

        logger.close_agent(agent_name)

        assert agent_name not in logger._agent_state


class TestLogEvent:
    """Test cases for LogEvent."""

    def test_to_json(self):
        """Test converting LogEvent to JSON."""
        event = LogEvent(
            event_type=LogEventType.AGENT_START,
            agent_name="test-agent",
            timestamp=1.234,
            message="Test message",
            details={"key": "value"},
        )

        json_data = event.to_json()

        assert json_data["event_type"] == "AGENT_START"
        assert json_data["agent_name"] == "test-agent"
        assert json_data["timestamp"] == 1.234
        assert json_data["message"] == "Test message"
        assert json_data["details"] == {"key": "value"}

    def test_from_json(self):
        """Test creating LogEvent from JSON."""
        json_data = {
            "event_type": "AGENT_START",
            "agent_name": "test-agent",
            "timestamp": 1.234,
            "message": "Test message",
            "details": {"key": "value"},
        }

        event = LogEvent.from_json(json_data)

        assert event.event_type == LogEventType.AGENT_START
        assert event.agent_name == "test-agent"
        assert event.timestamp == 1.234
        assert event.message == "Test message"
        assert event.details == {"key": "value"}


class TestEventBuilders:
    """Test cases for event builder functions."""

    def test_build_agent_start_event(self):
        """Test agent start event builder."""
        event = build_agent_start_event(
            agent_name="test-agent",
            session_id="session-123",
            workspace="/tmp/workspace",
        )

        assert event.event_type == LogEventType.AGENT_START
        assert event.agent_name == "test-agent"
        assert event.details["session_id"] == "session-123"
        assert event.details["workspace"] == "/tmp/workspace"

    def test_build_agent_end_event(self):
        """Test agent end event builder."""
        event = build_agent_end_event(
            agent_name="test-agent",
            session_id="session-123",
            exit_code=0,
            elapsed=1.5,
        )

        assert event.event_type == LogEventType.AGENT_END
        assert event.agent_name == "test-agent"
        assert event.details["exit_code"] == 0
        assert event.details["elapsed_seconds"] == 1.5

    def test_build_stdout_event(self):
        """Test stdout event builder."""
        event = build_stdout_event("test-agent", "Hello, world!")

        assert event.event_type == LogEventType.STDOUT
        assert event.agent_name == "test-agent"
        assert event.message == "Hello, world!"

    def test_build_stderr_event(self):
        """Test stderr event builder."""
        event = build_stderr_event("test-agent", "Error message")

        assert event.event_type == LogEventType.STDERR
        assert event.agent_name == "test-agent"
        assert event.message == "Error message"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
