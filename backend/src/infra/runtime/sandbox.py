"""NoSandbox — today's behavior, and the PERMANENT fallback (ROADMAP item 33).

Not a placeholder to delete once a real adapter (e.g. BubblewrapSandbox, item
34) exists: an environment without working OS-level confinement must keep
running real-mode tasks rather than silently degrading or refusing to run,
and `probe()` says so plainly rather than reporting a healthy sandbox.
CliAgentRunner (src/infra/runtime/cli_runner.py) consumes whichever adapter
it is given identically — it never branches on which one is active.
"""

from __future__ import annotations

from src.app.sandbox_port import SandboxPolicy, SandboxProbeResult


class NoSandbox:
    """Passes the command through unchanged."""

    def wrap(self, cmd: list[str], policy: SandboxPolicy) -> list[str]:
        return cmd

    def probe(self) -> SandboxProbeResult:
        return SandboxProbeResult(
            name="sandbox",
            ok=True,
            message="sandbox=disabled (NoSandbox) — task attempts run unconfined",
        )
