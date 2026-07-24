"""ROADMAP item 33: NoSandbox is a pure passthrough + honest probe, and
CliAgentRunner actually calls the injected Sandbox's wrap() with the
attempt's cwd as the policy's workdir before handing the command to
supervise_process — the seam a real adapter (item 34) plugs into without
any caller changing."""

from __future__ import annotations

from pathlib import Path

from src.app.sandbox_port import SandboxPolicy
from src.infra.runtime import process_supervisor
from src.infra.runtime.cli_runner import ClaudeCodeRunner
from src.infra.runtime.process_supervisor import ProcessSupervisorResult
from src.infra.runtime.sandbox import NoSandbox


def test_no_sandbox_passes_the_command_through_unchanged():
    cmd = ["claude", "-p", "prompt"]
    assert NoSandbox().wrap(cmd, SandboxPolicy(workdir="/tmp/x")) == cmd


def test_no_sandbox_probe_reports_disabled_not_healthy():
    probe = NoSandbox().probe()
    assert probe.ok is True
    assert "disabled" in probe.message.lower()


class _RecordingSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], SandboxPolicy]] = []

    def wrap(self, cmd: list[str], policy: SandboxPolicy) -> list[str]:
        self.calls.append((cmd, policy))
        return ["WRAPPED", *cmd]

    def probe(self):  # pragma: no cover - not exercised here
        raise NotImplementedError


def test_cli_agent_runner_wraps_the_command_through_its_sandbox(monkeypatch, tmp_path):
    sandbox = _RecordingSandbox()
    runner = ClaudeCodeRunner(api_key="claude-key", sandbox=sandbox)

    captured_cmd: list[str] = []

    def fake_supervise_process(cmd: list[str], **kwargs: object) -> ProcessSupervisorResult:
        captured_cmd.extend(cmd)
        return ProcessSupervisorResult(
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            log_path=Path(tmp_path / "log.jsonl"),
            stdout_bytes=2,
            stderr_bytes=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr(process_supervisor, "supervise_process", fake_supervise_process)
    monkeypatch.setattr(
        "src.infra.runtime.cli_runner.supervise_process", fake_supervise_process
    )

    runner._run_sync(prompt="hello", cwd=str(tmp_path), execution_env={}, observations=[])

    assert len(sandbox.calls) == 1
    wrapped_cmd, policy = sandbox.calls[0]
    assert wrapped_cmd[0] == "claude"
    assert policy.workdir == str(tmp_path)
    assert captured_cmd[0] == "WRAPPED"  # supervise_process received the WRAPPED command
