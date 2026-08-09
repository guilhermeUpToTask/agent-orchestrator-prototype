"""Boot a cycle's tree in a real container and check the application works.

This is the adapter behind the `cycle_verification` slot: verification modes
prove *a command exited 0 against this commit*, and this proves *the application
runs*. See `app/environment_port.py` for why the two are different questions.

The container binary is configuration (`environment.container_binary`): podman,
colima and rancher are CLI-compatible with docker for everything used here.

`verify()` MUST NOT raise. An acceptance run is advisory, and a crash inside it
must never take down the promotion or the publication gate it was observing —
so every failure path returns a verdict, and teardown runs on all of them.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import structlog

from agent_orchestrator.app.environment_port import AcceptanceVerdict, EnvironmentSpec

log = structlog.get_logger(__name__)

_MOUNT_POINT = "/app"

# Teardown must not inherit the caller's timeout: a scenario that timed out is
# exactly when a leaked container is most likely, and most expensive.
_TEARDOWN_TIMEOUT_SECONDS = 60
_HEALTHCHECK_PROBE_TIMEOUT_SECONDS = 30
_HEALTHCHECK_POLL_SECONDS = 1.0
_WORKTREE_TIMEOUT_SECONDS = 120

# How long the DAEMON may take to accept a detached create, which is not how
# long the APPLICATION may take to become healthy. `spec.startup_timeout_seconds`
# is the operator's budget for the latter and can legitimately be a few seconds;
# spending it on `run -d` meant that on a loaded machine the client call timed
# out while the daemon went on to create the container anyway — an `errored`
# verdict AND a leaked container. Found by the real-container suite under
# parallel load; no scripted CLI would have produced it.
_DAEMON_CALL_TIMEOUT_SECONDS = 120


class _CommandFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


@contextlib.contextmanager
def _checkout(repo: Path, ref: str, root: Path | None) -> Iterator[Path]:
    """A disposable worktree at `ref`.

    The run must see exactly that commit and never the developer's dirty
    working tree — an acceptance verdict attributed to a commit that was not
    what ran is worse than no verdict.
    """
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        tree = Path(tmp) / "tree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(tree), ref],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=_WORKTREE_TIMEOUT_SECONDS,
        )
        try:
            yield tree
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
                timeout=_WORKTREE_TIMEOUT_SECONDS,
            )


class ContainerEnvironment:
    def __init__(self, binary: str, workspace_root: Path | None = None) -> None:
        self._binary = binary
        self._workspace_root = workspace_root

    def verify(
        self, repo: Path, ref: str, spec: EnvironmentSpec | None
    ) -> AcceptanceVerdict:
        if spec is None:
            return AcceptanceVerdict(
                outcome="skipped",
                summary=(
                    "No project environment is configured, so the application "
                    "was not booted."
                ),
            )
        started = time.monotonic()
        try:
            return self._run(repo, ref, spec, started)
        except Exception as exc:  # verify() must not raise — see the docstring.
            log.warning("acceptance.errored", error=str(exc), ref=ref)
            return AcceptanceVerdict(
                outcome="errored",
                summary=f"The acceptance run could not complete: {exc}",
                duration_seconds=time.monotonic() - started,
            )

    def _run(
        self, repo: Path, ref: str, spec: EnvironmentSpec, started: float
    ) -> AcceptanceVerdict:
        if shutil.which(self._binary) is None:
            return AcceptanceVerdict(
                outcome="errored",
                summary=(
                    f"`{self._binary}` is not installed, so the application "
                    "was not booted."
                ),
                detail=(
                    f"Install {self._binary}, or set the "
                    "`environment.container_binary` config key to a container "
                    "CLI that is on PATH."
                ),
                duration_seconds=time.monotonic() - started,
            )

        name = f"aipom-acceptance-{uuid.uuid4().hex[:12]}"
        with _checkout(repo, ref, self._workspace_root) as tree:
            # The teardown `finally` must be armed BEFORE the start, not after
            # it succeeds: a create that fails or times out on the client side
            # can still have created the container daemon-side, and a run that
            # leaks containers is worse than one that reports nothing.
            try:
                try:
                    self._start(name, tree, spec)
                except _CommandFailed as exc:
                    return AcceptanceVerdict(
                        outcome="errored",
                        summary="The container did not start.",
                        detail=exc.output,
                        duration_seconds=time.monotonic() - started,
                    )
                return self._observe(name, spec, started)
            finally:
                self._teardown(name)

    def _teardown(self, name: str) -> None:
        """Remove the container, and never raise while doing it.

        This runs in a `finally`: an exception here would replace whatever
        verdict or error the run had actually produced with a teardown failure,
        which is both less informative and a way for `verify()` to raise.
        """
        try:
            self._exec(
                [self._binary, "rm", "-f", name],
                timeout=_TEARDOWN_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 — see the docstring.
            log.warning("acceptance.teardown_failed", container=name, error=str(exc))

    def _start(self, name: str, tree: Path, spec: EnvironmentSpec) -> None:
        cmd = [
            self._binary,
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{tree}:{_MOUNT_POINT}",
            "-w",
            _MOUNT_POINT,
        ]
        if spec.port:
            cmd += ["-p", f"{spec.port}:{spec.port}"]
        cmd.append(spec.image)
        if spec.command:
            cmd += ["sh", "-c", spec.command]
        # The daemon's budget, not the application's — see the constant.
        self._exec(cmd, timeout=_DAEMON_CALL_TIMEOUT_SECONDS)
        log.info("acceptance.container_started", container=name, image=spec.image)

    def _observe(
        self, name: str, spec: EnvironmentSpec, started: float
    ) -> AcceptanceVerdict:
        if spec.healthcheck and not self._await_health(name, spec):
            return AcceptanceVerdict(
                outcome="failed",
                summary="The application did not become healthy before the timeout.",
                detail=(
                    f"Healthcheck `{spec.healthcheck}` never succeeded within "
                    f"{spec.startup_timeout_seconds}s."
                ),
                duration_seconds=time.monotonic() - started,
            )

        transcript: list[str] = []
        for step in spec.scenario:
            proc = self._exec(
                [self._binary, "exec", name, "sh", "-c", step],
                timeout=spec.startup_timeout_seconds,
                check=False,
            )
            output = (proc.stdout + proc.stderr).strip()
            transcript.append(f"$ {step}\n{output}")
            if proc.returncode != 0:
                return AcceptanceVerdict(
                    outcome="failed",
                    summary=f"Scenario step failed: {step}",
                    detail="\n".join(transcript),
                    duration_seconds=time.monotonic() - started,
                )

        return AcceptanceVerdict(
            outcome="passed",
            summary=(
                f"The application booted and all {len(spec.scenario)} scenario "
                "step(s) succeeded."
            ),
            detail="\n".join(transcript),
            duration_seconds=time.monotonic() - started,
        )

    def _await_health(self, name: str, spec: EnvironmentSpec) -> bool:
        assert spec.healthcheck is not None
        deadline = time.monotonic() + spec.startup_timeout_seconds
        while time.monotonic() < deadline:
            proc = self._exec(
                [self._binary, "exec", name, "sh", "-c", spec.healthcheck],
                timeout=_HEALTHCHECK_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
            if proc.returncode == 0:
                return True
            time.sleep(_HEALTHCHECK_POLL_SECONDS)
        return False

    def _exec(
        self, cmd: list[str], timeout: int, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        # `check=False` to subprocess deliberately: this method's own `check`
        # raises `_CommandFailed` (carrying the output) rather than letting a
        # `CalledProcessError` escape with it stripped off.
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        if check and proc.returncode != 0:
            raise _CommandFailed((proc.stdout + proc.stderr).strip())
        return proc
