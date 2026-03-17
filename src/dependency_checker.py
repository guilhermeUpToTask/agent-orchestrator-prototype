"""
src/dependency_checker.py — Checks that all external tools the orchestrator
needs are present and reachable before starting or setting up.

Checked dependencies:
  redis   — Redis server must be reachable at the configured URL
  git     — git binary must be in PATH
  runtimes:
    gemini — Gemini CLI (`gemini` command, installed via npm)
    claude — Claude Code CLI (`claude` command, installed via npm)

The orchestrator requires:
  • redis  ✓
  • git    ✓
  • at least ONE runtime ✓

Usage:
    from src.dependency_checker import DependencyChecker
    checker = DependencyChecker(redis_url="redis://localhost:6379/0")
    report = checker.run()
    if not report.can_start:
        ...
"""

from __future__ import annotations

import shutil
import subprocess
import redis
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DepResult:
    """Result of a single dependency check."""

    name: str
    ok: bool
    message: str
    install_hint: str = ""
    is_runtime: bool = False


@dataclass
class DependencyReport:
    """Aggregated result of all dependency checks."""

    results: list[DepResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def redis_ok(self) -> bool:
        return self._get("redis").ok

    @property
    def git_ok(self) -> bool:
        return self._get("git").ok

    @property
    def any_runtime_ok(self) -> bool:
        return any(r.ok for r in self.results if r.is_runtime)

    @property
    def can_start(self) -> bool:
        """Minimum requirements: redis + git + at least one runtime."""
        return self.redis_ok and self.git_ok and self.any_runtime_ok

    def _get(self, name: str) -> DepResult:
        for r in self.results:
            if r.name == name:
                return r
        return DepResult(name=name, ok=False, message="not checked")

    def failing(self) -> list[DepResult]:
        return [r for r in self.results if not r.ok]


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def _check_redis(redis_url: str) -> DepResult:
    try:
        client = redis.from_url(redis_url, socket_connect_timeout=2, decode_responses=False)
        client.ping()
        return DepResult("redis", ok=True, message=f"reachable at {redis_url}")
    except Exception as exc:  # noqa: BLE001
        short = str(exc).split("\n")[0][:120]
        return DepResult(
            "redis",
            ok=False,
            message=short,
            install_hint="Start Redis: redis-server  or  docker run -p 6379:6379 redis",
        )


def _check_git() -> DepResult:
    if not shutil.which("git"):
        return DepResult(
            "git",
            ok=False,
            message="git not found in PATH",
            install_hint="Install git: https://git-scm.com/downloads",
        )
    try:
        out = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = out.stdout.strip()
        return DepResult("git", ok=True, message=version)
    except Exception as exc:  # noqa: BLE001
        return DepResult("git", ok=False, message=str(exc))


def _check_binary(
    name: str,
    cmd: str,
    install_hint: str,
    version_flag: str = "--version",
) -> DepResult:
    if not shutil.which(cmd):
        return DepResult(
            name,
            ok=False,
            message=f"`{cmd}` not found in PATH",
            install_hint=install_hint,
            is_runtime=True,
        )
    try:
        out = subprocess.run(
            [cmd, version_flag],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = (out.stdout or out.stderr).strip()
        version = raw[:100] if raw else f"{cmd} found"
        return DepResult(name, ok=True, message=version, is_runtime=True)
    except Exception as exc:  # noqa: BLE001
        return DepResult(name, ok=False, message=str(exc), is_runtime=True)


# ---------------------------------------------------------------------------
# Runtime check definitions
# ---------------------------------------------------------------------------

RUNTIME_DEFINITIONS: list[tuple[str, str, str]] = [
    # (display_name, binary, install_hint)
    (
        "gemini-cli",
        "gemini",
        "npm install -g @google/gemini-cli",
    ),
    (
        "claude-code",
        "claude",
        "npm install -g @anthropic-ai/claude-code",
    ),
]


# ---------------------------------------------------------------------------
# DependencyChecker
# ---------------------------------------------------------------------------


class DependencyChecker:
    """
    Runs all dependency checks and returns a DependencyReport.

    Parameters
    ----------
    redis_url:
        Connection string to test.  Defaults to ``redis://localhost:6379/0``.
    extra_checks:
        Optional additional check callables ``() -> DepResult`` for testing/
        extension.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        extra_checks: list[Callable[[], DepResult]] | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._extra_checks = extra_checks or []

    def run(self) -> DependencyReport:
        results: list[DepResult] = []

        results.append(_check_redis(self._redis_url))
        results.append(_check_git())
        for name, cmd, hint in RUNTIME_DEFINITIONS:
            results.append(_check_binary(name, cmd, hint))
        for fn in self._extra_checks:
            results.append(fn())

        return DependencyReport(results=results)
