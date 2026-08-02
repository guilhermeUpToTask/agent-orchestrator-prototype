"""The permanent no-environment fallback. See app/environment_port.py.

Not a placeholder. A project with nothing configured is the default and
supported state — most projects the orchestrator will be pointed at are
libraries and CLIs whose tests genuinely are the contract, and for those an
acceptance run has nothing to add. It reports `skipped`, which read models
distinguish from a pass, so an unconfigured project shows nothing rather than a
reassuring green.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agent_orchestrator.app.environment_port import (
    AcceptanceVerdict,
    EnvironmentSpec,
)

log = structlog.get_logger(__name__)


class NoEnvironment:
    def verify(
        self, repo: Path, ref: str, spec: EnvironmentSpec | None
    ) -> AcceptanceVerdict:
        log.debug("acceptance.skipped", ref=ref)
        return AcceptanceVerdict(
            outcome="skipped",
            summary="No project environment is configured, so the application was not booted.",
            detail=(
                "Verification proved the recorded commands exited as expected against "
                "these commits. It did not prove the application runs. Configure a "
                "project environment to add that check."
            ),
        )
