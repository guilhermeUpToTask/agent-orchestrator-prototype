"""Portable Git diff and command execution for orchestrator-owned verification."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

from src.app.ports import Clock, CommandExecution


class LocalVerificationExecutor:
    def __init__(
        self,
        clock: Clock,
        timeout_seconds: int = 900,
        output_limit: int = 8_000,
    ) -> None:
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._output_limit = output_limit

    async def changed_paths(
        self,
        workspace_path: str,
        base_ref: str | None = None,
    ) -> list[str]:
        return await asyncio.to_thread(
            self._changed_paths,
            Path(workspace_path),
            base_ref,
        )

    def _changed_paths(self, root: Path, base_ref: str | None) -> list[str]:
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        )
        paths = {
            line[3:].strip().split(" -> ")[-1]
            for line in status.stdout.splitlines()
            if len(line) >= 4
        }
        if base_ref is not None:
            committed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "diff",
                    "--name-only",
                    "--diff-filter=ACDMRTUXB",
                    f"{base_ref}...HEAD",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            paths.update(line.strip() for line in committed.stdout.splitlines() if line.strip())
        return sorted(paths)

    async def run(
        self,
        workspace_path: str,
        commands: list[str],
    ) -> list[CommandExecution]:
        outcomes: list[CommandExecution] = []
        for command in commands:
            outcomes.append(await asyncio.to_thread(self._run_one, Path(workspace_path), command))
        return outcomes

    def _run_one(self, root: Path, command: str) -> CommandExecution:
        started = self._clock.now()
        try:
            # No login shell (-l): login profiles may reset PATH and drop the
            # orchestrator's environment (venv), making tool resolution
            # machine-dependent. Verification must run with the worker's env.
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
            exit_code = result.returncode
            output = (result.stdout + "\n" + result.stderr)[-self._output_limit :]
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            output = f"verification timed out: {exc}"
        finished = self._clock.now()
        digest = hashlib.sha256(output.encode()).hexdigest()
        return CommandExecution(
            command=command,
            exit_code=exit_code,
            started_at=started,
            finished_at=finished,
            bounded_output_ref=f"sha256:{digest}",
        )
