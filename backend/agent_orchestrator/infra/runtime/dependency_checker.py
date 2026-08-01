"""
src/infra/runtime/dependency_checker.py — probes the external tools the real
agent runtimes need (ported from the pre-refactor checker, minus Redis — the
claim path is the SQLite lease now).

Checked dependencies:
  git     — git binary must be in PATH (the branching workspace)
  runtimes:
    pi     — pi-mono coding agent (`pi` command)
    claude — Claude Code CLI (`claude` command)
    gemini — Gemini CLI (`gemini` command)

Consumers: `/api/runner/status` reports the results to the settings UI, and
the worker logs a startup warning in agent_runner.mode=real when something is
missing. dry-run needs none of these — nothing here ever blocks startup.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DepResult:
    """Result of a single dependency probe."""

    name: str
    binary: str
    ok: bool
    message: str
    install_hint: str = ""
    is_runtime: bool = False


@dataclass(frozen=True)
class DependencyReport:
    """Aggregated result of all dependency probes."""

    results: list[DepResult] = field(default_factory=list)

    @property
    def git_ok(self) -> bool:
        return any(r.ok for r in self.results if r.name == "git")

    @property
    def any_runtime_ok(self) -> bool:
        return any(r.ok for r in self.results if r.is_runtime)

    @property
    def can_run_real(self) -> bool:
        """Minimum for agent_runner.mode=real: git + at least one runtime."""
        return self.git_ok and self.any_runtime_ok

    def failing(self) -> list[DepResult]:
        return [r for r in self.results if not r.ok]


# runtime_type -> (binary, install hint); the keys ARE the CLI runtime names
# the agent registry's runtime_type resolves to (dry-run needs no binary).
RUNTIME_DEFINITIONS: dict[str, tuple[str, str]] = {
    "pi": ("pi", "See pi-mono installation docs — build from source or npm"),
    "claude": ("claude", "npm install -g @anthropic-ai/claude-code"),
    "gemini": ("gemini", "npm install -g @google/gemini-cli"),
}


def _probe_binary(
    name: str,
    binary: str,
    install_hint: str,
    *,
    is_runtime: bool,
    version_flag: str = "--version",
) -> DepResult:
    if not shutil.which(binary):
        return DepResult(
            name=name,
            binary=binary,
            ok=False,
            message=f"`{binary}` not found in PATH",
            install_hint=install_hint,
            is_runtime=is_runtime,
        )
    try:
        out = subprocess.run([binary, version_flag], capture_output=True, text=True, timeout=5)
        raw = (out.stdout or out.stderr).strip()
        message = raw.splitlines()[0][:100] if raw else f"{binary} found"
        return DepResult(
            name=name,
            binary=binary,
            ok=True,
            message=message,
            is_runtime=is_runtime,
        )
    except Exception as exc:  # noqa: BLE001 — a broken binary is a failed probe
        return DepResult(
            name=name,
            binary=binary,
            ok=False,
            message=str(exc)[:120],
            install_hint=install_hint,
            is_runtime=is_runtime,
        )


def check_dependencies() -> DependencyReport:
    """Probe git + every CLI runtime binary. Never raises."""
    results = [
        _probe_binary(
            "git",
            "git",
            "Install git: https://git-scm.com/downloads",
            is_runtime=False,
        )
    ]
    for runtime, (binary, hint) in RUNTIME_DEFINITIONS.items():
        results.append(_probe_binary(runtime, binary, hint, is_runtime=True))
    return DependencyReport(results=results)
