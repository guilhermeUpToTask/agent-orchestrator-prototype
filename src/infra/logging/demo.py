#!/usr/bin/env python3
"""
Demo script for the live logging system.

This demonstrates how the logging system works with multiple concurrent agents.
"""
import time
import threading
from pathlib import Path

from src.infra.logging import (
    LiveLogger,
    build_agent_start_event,
    build_agent_end_event,
    build_stdout_event,
    build_stderr_event,
    build_llm_request_event,
    build_llm_response_event,
    build_tool_call_start_event,
    build_tool_call_end_event,
)


def simulate_agent_work(logger: LiveLogger, agent_name: str, delay: float = 0.1):
    """Simulate an agent performing work with logging."""
    # Register agent
    logger.register_agent(
        agent_name=agent_name,
        session_id=f"{agent_name}-session-{int(time.time())}",
        workspace_path=f"/tmp/workspace-{agent_name}",
    )

    # Emit start event
    logger.log_event(build_agent_start_event(
        agent_name=agent_name,
        session_id=f"{agent_name}-session-{int(time.time())}",
        workspace=f"/tmp/workspace-{agent_name}",
    ))

    # Simulate LLM request
    logger.log_event(build_llm_request_event(
        agent_name=agent_name,
        model="gpt-4",
        prompt_preview="Write a function to calculate fibonacci numbers...",
    ))

    time.sleep(delay)

    # Simulate tool call
    logger.log_event(build_tool_call_start_event(
        agent_name=agent_name,
        tool_name="read_file",
        arguments={"path": "src/main.py"},
    ))

    logger.log_event(build_stdout_event(agent_name, "Reading file: src/main.py"))
    time.sleep(delay)

    logger.log_event(build_tool_call_end_event(
        agent_name=agent_name,
        tool_name="read_file",
        result_preview="# Contents of main.py\n\ndef main():\n    print('Hello')",
    ))

    # Simulate more stdout
    logger.log_event(build_stdout_event(agent_name, "Analyzing code structure..."))
    time.sleep(delay)
    logger.log_event(build_stdout_event(agent_name, "Found 3 functions to refactor"))

    # Simulate stderr (warning)
    logger.log_event(build_stderr_event(agent_name, "Warning: deprecated API usage detected"))

    # Simulate LLM response
    logger.log_event(build_llm_response_event(
        agent_name=agent_name,
        model="gpt-4",
        response_preview="Here's the refactored code...",
    ))

    # Simulate completion
    logger.log_event(build_stdout_event(agent_name, "Task completed successfully"))

    # Emit end event
    logger.log_event(build_agent_end_event(
        agent_name=agent_name,
        session_id=f"{agent_name}-session-{int(time.time())}",
        exit_code=0,
        elapsed=1.5,
    ))

    # Close agent session
    logger.close_agent(agent_name)


def main():
    """Run demo with multiple concurrent agents."""
    print("=" * 70)
    print("Live Logging Demo - Concurrent Agent Simulation")
    print("=" * 70)
    print()

    # Create logger with JSON output
    json_dir = Path("/tmp/orchestrator_logs")
    json_dir.mkdir(parents=True, exist_ok=True)

    logger = LiveLogger(json_log_dir=json_dir)

    # Simulate 3 agents running concurrently
    agents = ["pi", "gemini", "claude"]
    threads = []

    for agent in agents:
        t = threading.Thread(
            target=simulate_agent_work,
            args=(logger, agent, 0.15),
            name=f"agent-{agent}",
        )
        threads.append(t)
        t.start()

    # Wait for all agents to complete
    for t in threads:
        t.join()

    print()
    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)
    print()
    print(f"JSON logs saved to: {json_dir}")
    print()
    print("Example JSON log entries:")
    print("-" * 70)

    # Show example of JSON logs
    log_files = list(json_dir.glob("*.jsonl"))
    if log_files:
        with open(log_files[0]) as f:
            import json
            for i, line in enumerate(f):
                if i >= 2:  # Show first 2 events
                    break
                event = json.loads(line)
                print(f"  {event['agent_name']} [{event['event_type']}]: {event['message'][:50]}...")

    print()
    print("To view all logs:")
    print(f"  cat {json_dir}/*.jsonl | jq .")


if __name__ == "__main__":
    main()
