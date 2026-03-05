#!/usr/bin/env python3
"""
scripts/demo.py — End-to-end demo (dry-run mode).

Usage:
  AGENT_MODE=dry-run python scripts/demo.py

What it does:
  1. Registers a worker agent
  2. Creates a sample task (YAML on disk)
  3. Task Manager assigns the task
  4. Worker processes it (dry-run: stubs out files + commit)
  5. Prints final state
"""
from __future__ import annotations

import sys
import os
import json
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("AGENT_MODE", "dry-run")
os.environ.setdefault("AGENT_ID", "agent-worker-001")
os.environ.setdefault("TASKS_DIR", "workflow/tasks")
os.environ.setdefault("REGISTRY_PATH", "workflow/agents/registry.json")

import structlog
structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(),
    ]
)

from src.core.models import (
    AgentProps, AgentSelector, ExecutionSpec, TaskAggregate, TrustLevel
)
from src.infra.factory import (
    build_task_repo, build_agent_registry, build_event_port,
    build_lease_port, build_worker_handler, build_task_manager_handler,
)


def main():
    print("\n" + "=" * 60)
    print("  Agent Orchestrator — E2E Demo (dry-run mode)")
    print("=" * 60 + "\n")

    # ----------------------------------------------------------------
    # Setup directories
    # ----------------------------------------------------------------
    Path("workflow/tasks").mkdir(parents=True, exist_ok=True)
    Path("workflow/agents").mkdir(parents=True, exist_ok=True)
    Path("workflow/logs").mkdir(parents=True, exist_ok=True)
    Path("workflow/events").mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # 1. Register worker agent
    # ----------------------------------------------------------------
    registry = build_agent_registry()
    agent = AgentProps(
        agent_id="agent-worker-001",
        name="Backend Dev Worker",
        capabilities=["backend_dev", "python"],
        version="1.2.0",
        tools=["pytest", "git"],
        trust_level=TrustLevel.HIGH,
        max_concurrent_tasks=3,
    )
    registry.register(agent)
    print(f"✓ Registered agent: {agent.agent_id}")

    # ----------------------------------------------------------------
    # 2. Create sample task
    # ----------------------------------------------------------------
    task_repo = build_task_repo()
    task = TaskAggregate(
        task_id="demo-task-001",
        feature_id="feat-auth",
        title="Implement POST /login",
        description="Add login endpoint that validates credentials and returns JWT.",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            constraints={"language": "python", "framework": "fastapi"},
            files_allowed_to_modify=["app/auth.py", "tests/test_auth.py"],
            test_command=None,  # skip real tests in demo
            acceptance_criteria=["All tests pass", "Endpoint follows API spec"],
        ),
    )
    task_repo.save(task)
    print(f"✓ Created task: {task.task_id} (status: {task.status.value})")

    # ----------------------------------------------------------------
    # 3. Task Manager assigns
    # ----------------------------------------------------------------
    tm = build_task_manager_handler()
    assigned = tm.handle_task_created(task.task_id)
    if not assigned:
        print("✗ Task Manager could not assign task — no eligible agent!")
        return

    task_after_assign = task_repo.load(task.task_id)
    print(f"✓ Task assigned → agent: {task_after_assign.assignment.agent_id}")
    print(f"  state_version: {task_after_assign.state_version}")

    # ----------------------------------------------------------------
    # 4. Worker processes task
    # ----------------------------------------------------------------
    print("\n  Starting worker...")
    worker = build_worker_handler()
    worker.process(task.task_id, "proj-demo")

    # ----------------------------------------------------------------
    # 5. Final state
    # ----------------------------------------------------------------
    final = task_repo.load(task.task_id)
    print(f"\n✓ Final status: {final.status.value}")
    if final.result:
        print(f"  commit_sha: {final.result.commit_sha}")
        print(f"  branch:     {final.result.branch}")
        print(f"  files:      {final.result.modified_files}")

    print("\n  History:")
    for entry in final.history:
        print(f"    [{entry.timestamp.strftime('%H:%M:%S')}] {entry.event} (by {entry.actor})")

    print("\n  Events published:")
    events = build_event_port()
    # Re-build event port (stateful in-memory) — for demo we re-use tm's ref
    # Note: in dry-run mode each build_*() creates a fresh in-memory instance.
    # For demo output, just read the task history.
    event_types = [h.event for h in final.history]
    for et in event_types:
        print(f"    • {et}")

    print("\n" + "=" * 60)
    print("  Demo complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
