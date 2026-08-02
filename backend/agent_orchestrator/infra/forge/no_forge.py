"""The permanent no-forge fallback. See agent_orchestrator/app/forge_port.py.

Not a placeholder: an installation with no GitHub token is a supported
configuration, not a broken one, on the same principle as `NoSandbox`. It
refuses loudly with an actionable message rather than degrading silently.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.app.forge_port import ForgeNotConfiguredError, PullRequestRef

_MESSAGE = (
    "No forge is bound to this project, so the orchestrator cannot open a pull "
    "request for you. Bind one under Settings -> Projects -> delivery, or record "
    "the disposition yourself with the reference you used."
)


class NoForge:
    def push_branch(self, repo: Path, branch: str) -> None:
        raise ForgeNotConfiguredError(_MESSAGE)

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        raise ForgeNotConfiguredError(_MESSAGE)
