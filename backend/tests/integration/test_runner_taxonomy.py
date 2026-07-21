"""The CLI runner against a scripted fake CLI: success path, and every
FailureKind classification the shared taxonomy defines (roadmap 2.4 #12)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app.ports import TaskFailed
from src.app.runtime_failures import safe_runtime_tail
from src.app.testing.fakes import CollectingEventSink
from src.app.testing.observations import InMemoryObservationRepository
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind
from src.infra.runtime.cli_runner import CliAgentRunner
from src.infra.runtime.pi_protocol import extract_stream_error, parse_pi_events
from src.infra.runtime.taxonomy import classify_failure, normalize_failure

pytestmark = pytest.mark.integration


class ScriptedCliRunner(CliAgentRunner):
    """Runs an arbitrary executable — the test controls the CLI's behavior."""

    def __init__(self, executable: str, timeout_seconds: int = 5, observation_repository=None) -> None:
        super().__init__(timeout_seconds, observation_repository=observation_repository)
        self._executable = executable

    @property
    def log_prefix(self) -> str:
        return "scripted"

    def _build_cmd(self, prompt: str) -> list[str]:
        return [self._executable, prompt]

    def _env(self) -> dict[str, str]:
        return dict(os.environ)


class ScriptedPiRunner(ScriptedCliRunner):
    """A pi-shaped runner: interprets in-band stream errors like PiAgentRunner."""

    @property
    def log_prefix(self) -> str:
        return "pi"

    def _detect_stream_error(self, stdout: str) -> str | None:
        return extract_stream_error(stdout)


# The exact record pi emits on Nvidia free-tier exhaustion while exiting 0.
_PI_RATE_LIMIT_STREAM = json.dumps(
    {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
            "errorMessage": (
                "Upstream error from Nvidia: ResourceExhausted: "
                "Worker local total request limit reached (32/32)"
            ),
        },
    }
)


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


def run(runner, workdir, idempotency_key="p1:g1:t1"):
    class _H:
        path = str(workdir)

    sink = CollectingEventSink()
    result = asyncio.run(
        runner.run(
            task(),
            spec(),
            idempotency_key=idempotency_key,
            event_sink=sink,
            workspace=_H(),
        )
    )
    return result, sink


def test_success_returns_result_and_emits_events(tmp_path):
    cli = make_cli(tmp_path, 'echo "did the work"; exit 0')
    result, sink = run(ScriptedCliRunner(cli), tmp_path)
    assert result.status == "success"
    assert "did the work" in result.output
    assert [e.type for e in sink.events] == [
        "agent.started",
        "runtime.output",
        "agent.finished",
    ]
    assert sink.events[1].payload["chunk"] == "did the work"
    assert all(e.task_id == "t1" and e.attempt == 1 for e in sink.events)


def test_process_observations_are_persisted_on_success(tmp_path):
    cli = make_cli(tmp_path, 'echo "did the work"; exit 0')
    repository = InMemoryObservationRepository(lambda: datetime.now(timezone.utc))

    result, _ = run(ScriptedCliRunner(cli, observation_repository=repository), tmp_path)

    assert result.status == "success"
    assert [item.observation.kind.value for item in repository.observations] == [
        "process.started",
        "process.exited",
    ]


def test_process_observations_are_persisted_before_timeout_failure(tmp_path):
    cli = make_cli(tmp_path, "sleep 10")
    repository = InMemoryObservationRepository(lambda: datetime.now(timezone.utc))

    with pytest.raises(TaskFailed) as exc_info:
        run(
            ScriptedCliRunner(cli, timeout_seconds=1, observation_repository=repository),
            tmp_path,
        )

    assert exc_info.value.kind == FailureKind.TIMEOUT
    assert [item.observation.kind.value for item in repository.observations] == [
        "process.started",
        "process.timed_out",
    ]


def test_execution_correlation_is_allowlisted_into_subprocess_env(tmp_path):
    cli = make_cli(
        tmp_path,
        'printf "%s|%s|%s|%s|%s|%s\\n" '
        '"$ORCHESTRATOR_PLAN_ID" "$ORCHESTRATOR_GOAL_ID" '
        '"$ORCHESTRATOR_TASK_ID" "$ORCHESTRATOR_RUN_ID" '
        '"$ORCHESTRATOR_ATTEMPT_NUMBER" "$ORCHESTRATOR_ATTEMPT_ID"',
    )
    result, _ = run(
        ScriptedCliRunner(cli),
        tmp_path,
        idempotency_key="p1:g1:t1:run-1:7:attempt-1",
    )
    assert result.output.strip() == "p1|g1|t1|run-1|7|attempt-1"


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


def test_nvidia_resource_exhausted_normalizes_retry_evidence_without_secrets():
    failure = normalize_failure(
        stderr=(
            "NVIDIA NIM ResourceExhausted RESOURCE_EXHAUSTED; retry-after: 90s; "
            "api_key=sk-abcdefghijklmnop Authorization: Bearer token-secret"
        ),
        runtime="pi",
        provider_id="nvidia",
        model_id="nvidia:nemotron",
        exit_code=1,
    )
    assert failure.kind == FailureKind.RATE_LIMIT
    assert failure.provider_code == "RESOURCE_EXHAUSTED"
    assert failure.retry_after_seconds == 90
    assert failure.retryable
    assert "sk-" not in failure.safe_message
    assert "token-secret" not in failure.stderr_tail


def test_pi_in_band_rate_limit_is_retryable_not_empty_success(tmp_path):
    # Regression (walkthrough finding #16): pi exits 0 but reports the upstream
    # rate limit inside its NDJSON stream. The empty result must NOT pass as a
    # successful run (which a downstream test-author stage then mislabels as a
    # terminal "produced no executable checks"); it must classify as a
    # RETRYABLE rate limit so the durable backoff waits out the transient cap.
    cli = make_cli(tmp_path, f"cat <<'PIEOF'\n{_PI_RATE_LIMIT_STREAM}\nPIEOF\nexit 0")
    with pytest.raises(TaskFailed) as exc_info:
        run(ScriptedPiRunner(cli), tmp_path)
    assert exc_info.value.kind == FailureKind.RATE_LIMIT
    assert exc_info.value.failure is not None and exc_info.value.failure.retryable


def test_failed_event_carries_kind_for_in_band_stream_error(tmp_path):
    cli = make_cli(tmp_path, f"cat <<'PIEOF'\n{_PI_RATE_LIMIT_STREAM}\nPIEOF\nexit 0")
    runner = ScriptedPiRunner(cli)
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


def test_base_runner_does_not_interpret_pi_style_stream(tmp_path):
    # A non-pi runtime has no in-band error hook: exit 0 stays a success.
    cli = make_cli(tmp_path, f"cat <<'PIEOF'\n{_PI_RATE_LIMIT_STREAM}\nPIEOF\nexit 0")
    result, _ = run(ScriptedCliRunner(cli), tmp_path)
    assert result.status == "success"


def test_pi_events_are_allowlisted_and_prompt_fields_are_dropped():
    events = parse_pi_events(
        '{"type":"model.usage","payload":{"total_tokens":7,'
        '"input_tokens":5,"prompt":"private","api_key":"secret"}}\n'
        '{"type":"unknown","payload":{"total_tokens":99}}\n'
        "not json"
    )
    assert events == [("model.usage", {"input_tokens": 5, "total_tokens": 7})]
    assert "secret" not in safe_runtime_tail("api_key=secret Bearer opaque-token")
