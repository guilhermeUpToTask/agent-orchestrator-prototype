"""
src/infra/runtime/cli_runner.py — CLI agent runners (the async AgentRunner port).

A runner executes ONE task as a one-shot CLI subprocess in the workspace
directory and returns a TaskResult (or raises TaskFailed carrying a typed
FailureKind). It knows NOTHING about retries, backoff or ordering — those are
orchestration decisions; the runner is a pure "execute this task now" hand.

Subclasses supply the command line and environment (pi / claude / gemini —
ported from the pre-refactor runtimes). The subprocess is blocking, hopped off
the event loop via asyncio.to_thread so a long agent run never blocks the
worker.

Streamed NDJSON events (the full pi stdio handshake) are roadmap 2.4 — the seam
is isolated in pi_protocol.py; today the sink gets start/finish events so every
attempt is observable end-to-end.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from abc import ABC, abstractmethod

import structlog

from src.app.ports import AgentEventSink, TaskFailed, WorkspaceHandle
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.task import Task
from src.domain.events.agent_events import AgentEvent
from src.domain.value_objects.tasks_vos import TaskResult
from src.infra.runtime.taxonomy import classify_failure

log = structlog.get_logger(__name__)

_OUTPUT_TAIL_CHARS = 8_000  # keep TaskResult.output bounded


def build_task_prompt(task: Task, spec: AgentSpec) -> str:
    """Markdown task contract handed to the CLI agent. Project-level conventions
    live in the workspace AGENTS.md — not here — so they apply consistently
    across runtimes."""
    capabilities = (
        ", ".join(task.required_capabilities) or "(none declared)"
    )
    return (
        f"# Task: {task.name}\n\n"
        f"{task.description}\n\n"
        f"## Your role\n{spec.role}\n\n"
        f"## Instructions\n{spec.instructions or '(none)'}\n\n"
        f"## Required capabilities\n{capabilities}\n\n"
        f"---\n"
        f"Task ID: `{task.id}` | Attempt: {task.attempt}"
    )


class CliAgentRunner(ABC):
    """Base for one-shot CLI agent subprocesses."""

    def __init__(self, timeout_seconds: int = 600) -> None:
        self._timeout = timeout_seconds

    @property
    @abstractmethod
    def log_prefix(self) -> str: ...

    @abstractmethod
    def _build_cmd(self, prompt: str) -> list[str]: ...

    @abstractmethod
    def _env(self) -> dict[str, str]: ...

    async def run(
        self,
        task: Task,
        spec: AgentSpec,
        *,
        idempotency_key: str,
        event_sink: AgentEventSink,
        workspace: WorkspaceHandle,
    ) -> TaskResult:
        plan_id = idempotency_key.split(":")[0]
        prompt = build_task_prompt(task, spec)

        await event_sink.emit(
            AgentEvent(
                plan_id=plan_id,
                task_id=task.id,
                attempt=task.attempt,
                seq=0,
                type="agent.started",
                payload={"runtime": self.log_prefix, "cwd": workspace.path},
            )
        )
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(self._run_sync, prompt, workspace.path)
        except TaskFailed as exc:
            await event_sink.emit(
                AgentEvent(
                    plan_id=plan_id,
                    task_id=task.id,
                    attempt=task.attempt,
                    seq=1,
                    type="agent.failed",
                    payload={
                        "kind": exc.kind.value if exc.kind else "unknown",
                        "reason": exc.reason[:500],
                    },
                )
            )
            raise
        elapsed = round(time.monotonic() - started, 2)
        await event_sink.emit(
            AgentEvent(
                plan_id=plan_id,
                task_id=task.id,
                attempt=task.attempt,
                seq=1,
                type="agent.finished",
                payload={"elapsed_seconds": str(elapsed)},
            )
        )
        return result

    def _run_sync(self, prompt: str, cwd: str) -> TaskResult:
        cmd = self._build_cmd(prompt)
        log.info(f"{self.log_prefix}.running", cwd=cwd, timeout=self._timeout)
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskFailed(
                f"{self.log_prefix} timed out after {self._timeout}s",
                classify_failure("", timed_out=True),
            ) from exc
        except FileNotFoundError as exc:
            raise TaskFailed(
                f"{self.log_prefix} CLI not found: {cmd[0]!r}",
                classify_failure(str(exc)),
            ) from exc

        if proc.returncode != 0:
            output = f"{proc.stdout}\n{proc.stderr}"
            kind = classify_failure(output)
            log.warning(
                f"{self.log_prefix}.failed",
                exit_code=proc.returncode,
                kind=kind.value,
            )
            raise TaskFailed(
                f"{self.log_prefix} exited {proc.returncode}: "
                f"{proc.stderr.strip()[-500:] or proc.stdout.strip()[-500:]}",
                kind,
            )

        log.info(f"{self.log_prefix}.finished", exit_code=0)
        return TaskResult.success(
            proc.stdout[-_OUTPUT_TAIL_CHARS:],
            metadata={"runtime": self.log_prefix, "exit_code": "0"},
        )


# Backend -> the env var pi reads for that provider (pi's env-api-keys.ts).
_PI_BACKEND_ENV_VAR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class PiAgentRunner(CliAgentRunner):
    """Runs `pi --model <m> -p "<prompt>"` in the workspace (pi-mono CLI)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        backend: str = "anthropic",
        extra_flags: list[str] | None = None,
        timeout_seconds: int = 600,
    ) -> None:
        if backend not in _PI_BACKEND_ENV_VAR:
            raise ValueError(
                f"Invalid pi backend '{backend}'. "
                f"Valid: {', '.join(sorted(_PI_BACKEND_ENV_VAR))}"
            )
        if not api_key:
            raise ValueError(
                f"PiAgentRunner requires an api_key for backend '{backend}'"
            )
        super().__init__(timeout_seconds)
        self._api_key = api_key
        self._model = model
        self._backend = backend
        self._extra_flags = extra_flags or []

    @property
    def log_prefix(self) -> str:
        return "pi"

    def _build_cmd(self, prompt: str) -> list[str]:
        return ["pi", "--model", self._model, "-p", prompt, *self._extra_flags]

    def _env(self) -> dict[str, str]:
        return {**os.environ, _PI_BACKEND_ENV_VAR[self._backend]: self._api_key}


class ClaudeCodeRunner(CliAgentRunner):
    """Runs `claude --dangerously-skip-permissions -p "<prompt>"`."""

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        extra_flags: list[str] | None = None,
        timeout_seconds: int = 600,
    ) -> None:
        super().__init__(timeout_seconds)
        self._api_key = api_key
        self._model = model
        self._extra_flags = extra_flags or []

    @property
    def log_prefix(self) -> str:
        return "claude"

    def _build_cmd(self, prompt: str) -> list[str]:
        cmd = ["claude", "--dangerously-skip-permissions", "-p", prompt]
        if self._model:
            cmd += ["--model", self._model]
        return cmd + self._extra_flags

    def _env(self) -> dict[str, str]:
        return {**os.environ, "ANTHROPIC_API_KEY": self._api_key}


class GeminiRunner(CliAgentRunner):
    """Runs `gemini --model=<m> --yolo -p "<prompt>"`."""

    def __init__(
        self,
        api_key: str,
        model: str,
        extra_flags: list[str] | None = None,
        timeout_seconds: int = 600,
    ) -> None:
        super().__init__(timeout_seconds)
        self._api_key = api_key
        self._model = model
        self._extra_flags = extra_flags or []

    @property
    def log_prefix(self) -> str:
        return "gemini"

    def _build_cmd(self, prompt: str) -> list[str]:
        return [
            "gemini",
            f"--model={self._model}",
            "--yolo",
            "-p",
            prompt,
            *self._extra_flags,
        ]

    def _env(self) -> dict[str, str]:
        return {
            **os.environ,
            "GEMINI_API_KEY": self._api_key,
            "GEMINI_MODEL": self._model,
        }
