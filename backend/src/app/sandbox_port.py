"""The Sandbox port (ROADMAP item 33): confines a task-attempt subprocess at
the OS level, if a real adapter is configured. Deliberately NOT a domain
concept — the frozen domain never sees this type, and even ExecutionHandler
never touches it directly; only the infra CLI runner
(src/infra/runtime/cli_runner.py) consumes it, wrapping the exact command it
was already going to run. Adapters live in infra: `NoSandbox`
(src/infra/runtime/sandbox.py) is today's behavior and the PERMANENT
fallback, not a placeholder — an environment without working confinement
must keep running real-mode tasks, loudly reporting sandbox=disabled rather
than silently degrading or refusing to run. A real adapter (e.g.
BubblewrapSandbox) plugs in beside it without any caller changing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxPolicy:
    """What ONE task attempt's confinement should look like. `workdir` is the
    per-attempt git worktree (framework already binds `cwd` to it) — the sole
    writable path a real adapter would grant; everything else it confines is
    read-only or hidden. Deliberately minimal for the port-only slice: extend
    fields here as a real adapter needs them, never change wrap()'s
    signature."""

    workdir: str
    network: bool = True


@dataclass(frozen=True)
class SandboxProbeResult:
    """Mirrors DepResult's shape (infra/runtime/dependency_checker.py) so a
    sandbox probe can sit alongside the binary probes in
    GET /api/runner/status without a special case."""

    name: str
    ok: bool
    message: str


@runtime_checkable
class Sandbox(Protocol):
    """`wrap` is pure and synchronous: given the real CLI invocation and its
    policy, return the (possibly confinement-prefixed) command to actually
    exec. The caller consumes this blindly — it does not know or care
    whether confinement is real."""

    def wrap(self, cmd: list[str], policy: SandboxPolicy) -> list[str]: ...

    def probe(self) -> SandboxProbeResult: ...
