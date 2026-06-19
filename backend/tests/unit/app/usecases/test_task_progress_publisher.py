"""
tests/unit/app/usecases/test_task_progress_publisher.py

The throttled agent-output publisher built by TaskExecuteUseCase: it batches
lines into task.progress events and flushes the tail.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.app.usecases.task_execute import TaskExecuteUseCase
from src.domain import AgentSelector, Assignment, ExecutionSpec, TaskAggregate, TaskStatus


def _uc(events):
    return TaskExecuteUseCase(
        repo_url="file:///r",
        task_repo=MagicMock(),
        agent_registry=MagicMock(),
        event_port=events,
        lease_port=MagicMock(),
        git_workspace=MagicMock(),
        runtime_factory=MagicMock(),
        logs_port=MagicMock(),
        test_runner=MagicMock(),
    )


def _task() -> TaskAggregate:
    t = TaskAggregate(
        task_id="t-1", feature_id="g-1", title="T", description="D",
        agent_selector=AgentSelector(required_capability="code:backend"),
        execution=ExecutionSpec(type="code:backend"),
        status=TaskStatus.IN_PROGRESS,
    )
    t.assignment = Assignment(agent_id="a-1", lease_seconds=300)
    return t


def test_first_line_emits_immediately_then_batches():
    events = MagicMock()
    cb, flush = _uc(events)._make_progress_publisher(_task())

    cb("compiling…")           # first line → immediate publish (low latency)
    first = events.publish.call_args[0][0]
    assert first.type == "task.progress"
    assert first.payload["task_id"] == "t-1"
    assert first.payload["lines"] == ["compiling…"]

    cb("running tests…")       # within throttle window → buffered
    flush()                    # tail flush emits the buffered line
    assert events.publish.call_args[0][0].payload["lines"] == ["running tests…"]
    assert events.publish.call_count == 2


def test_blank_lines_are_ignored():
    events = MagicMock()
    cb, flush = _uc(events)._make_progress_publisher(_task())
    cb("   ")
    cb("")
    flush()
    events.publish.assert_not_called()


def test_batch_flushes_when_buffer_is_large():
    events = MagicMock()
    cb, _ = _uc(events)._make_progress_publisher(_task())
    # line 0 flushes immediately; lines 1..50 then hit the 50-line batch cap →
    # a second auto-flush without waiting for the tail.
    for i in range(51):
        cb(f"line {i}")
    assert events.publish.call_count >= 2
