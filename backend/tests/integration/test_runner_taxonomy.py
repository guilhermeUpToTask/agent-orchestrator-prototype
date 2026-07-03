"""The CLI runner against a scripted fake CLI: success path, and every
FailureKind classification the shared taxonomy defines (roadmap 2.4 #12)."""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from src.app.ports import TaskFailed
from src.app.testing.fakes import CollectingEventSink
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind
from src.infra.runtime.cli_runner import CliAgentRunner
from src.infra.runtime.taxonomy import classify_failure

pytestmark = pytest.mark.integration


class ScriptedCliRunner(CliAgentRunner):
    """Runs an arbitrary executable — the test controls the CLI's behavior."""

    def __init__(self, executable: str, timeout_seconds: int = 5) -> None:
        super().__init__(timeout_seconds)
        self._executable = executable

    @property
    def log_prefix(self) -> str:
        return "scripted"

    def _build_cmd(self, prompt: str) -> list[str]:
        return [self._executable, prompt]

    def _env(self) -> dict[str, str]:
        return dict(os.environ)


def make_cli(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-agent"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def task():
    return Task(id="t1", name="do it", position=0, description="desc", attempt=1)


def spec():
    return AgentSpec(
        id="a1",
        name="A",
        role="implementer",
        model_role="smart",
        instructions="be careful",
        default_retry=RetryPolicy(),
    )


def run(runner, workdir):
    class _H:
        path = str(workdir)

    sink = CollectingEventSink()
    result = asyncio.run(
        runner.run(
            task(), spec(), idempotency_key="p1:g1:t1", event_sink=sink, workspace=_H()
        )
    )
    return result, sink


def test_success_returns_result_and_emits_events(tmp_path):
    cli = make_cli(tmp_path, 'echo "did the work"; exit 0')
    result, sink = run(ScriptedCliRunner(cli), tmp_path)
    assert result.status == "success"
    assert "did the work" in result.output
    assert [e.type for e in sink.events] == ["agent.started", "agent.finished"]
    assert all(e.task_id == "t1" and e.attempt == 1 for e in sink.events)


@pytest.mark.parametrize(
    "stderr_line,expected",
    [
        ("Error: rate limit exceeded (429)", FailureKind.RATE_LIMIT),
        ("Error: invalid API key provided", FailureKind.AUTH_ERROR),
        ("Error: prompt is too long: context length exceeded", FailureKind.TOKEN_LIMIT),
        ("Error: getaddrinfo ENOTFOUND api.example.com", FailureKind.CONNECTION_ERROR),
        ("Error: something exploded in a tool", FailureKind.TOOL_ERROR),
    ],
)
def test_failures_classified_by_taxonomy(tmp_path, stderr_line, expected):
    cli = make_cli(tmp_path, f'echo "{stderr_line}" >&2; exit 1')
    with pytest.raises(TaskFailed) as exc_info:
        run(ScriptedCliRunner(cli), tmp_path)
    assert exc_info.value.kind == expected


def test_timeout_classified_as_timeout(tmp_path):
    cli = make_cli(tmp_path, "sleep 10")
    with pytest.raises(TaskFailed) as exc_info:
        run(ScriptedCliRunner(cli, timeout_seconds=1), tmp_path)
    assert exc_info.value.kind == FailureKind.TIMEOUT


def test_missing_cli_binary_is_a_typed_failure(tmp_path):
    with pytest.raises(TaskFailed):
        run(ScriptedCliRunner(str(tmp_path / "does-not-exist")), tmp_path)


def test_failure_event_carries_kind(tmp_path):
    cli = make_cli(tmp_path, 'echo "rate limit" >&2; exit 1')
    runner = ScriptedCliRunner(cli)
    sink = CollectingEventSink()

    class _H:
        path = str(tmp_path)

    with pytest.raises(TaskFailed):
        asyncio.run(
            runner.run(
                task(),
                spec(),
                idempotency_key="p1:g1:t1",
                event_sink=sink,
                workspace=_H(),
            )
        )
    assert [e.type for e in sink.events] == ["agent.started", "agent.failed"]
    assert sink.events[1].payload["kind"] == "rate_limit"


def test_classifier_terminal_kinds_align_with_retry_policy():
    """The taxonomy and RetryPolicy must agree on what is terminal."""
    policy = RetryPolicy()
    assert not policy.should_retry(1, classify_failure("invalid api key"))
    assert not policy.should_retry(1, classify_failure("context length exceeded"))
    assert policy.should_retry(1, classify_failure("rate limit"))
    assert policy.should_retry(1, classify_failure("ECONNRESET"))
    assert policy.should_retry(1, classify_failure("", timed_out=True))
    assert policy.should_retry(1, classify_failure("mystery explosion"))
