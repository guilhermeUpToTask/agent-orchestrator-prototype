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

from praxis_orchestrator.app.environment_port import (
    AcceptanceVerdict,
    EnvironmentSpec,
)

log = structlog.get_logger(__name__)


class NoEnvironment:
    def verify(
        self, repo: Path, ref: str, spec: EnvironmentSpec | None
    ) -> AcceptanceVerdict:
        log.debug("acceptance.skipped", ref=ref, spec_configured=spec is not None)
        if spec is not None:
            # The project DID author a boot, and this adapter still ran — so the
            # orchestrator-scoped `environment.mode` is what selected the
            # fallback, not the operator's project config. Saying "nothing is
            # configured" here is simply false, and it costs a real diagnosis:
            # during the P8.6 demo run every visible fact pointed at the project
            # config, which was correct all along, while the actual cause was a
            # mode set after the worker had already resolved its adapter.
            #
            # Still `skipped` and still advisory — the acceptance run must never
            # take down the gate it observes, and an unselected adapter is not a
            # failure of the application. Only the explanation changes, which on
            # a skip IS the entire product.
            return AcceptanceVerdict(
                outcome="skipped",
                summary=(
                    "This project has an environment configured, but the acceptance "
                    "run is not enabled, so the application was not booted."
                ),
                detail=(
                    f"The project's boot spec was read (image `{spec.image}`), so the "
                    "project-scoped `environment.*` keys are fine. What selected this "
                    "no-op adapter is the orchestrator-scoped `environment.mode`, "
                    "which must be `container`. Note it is read once per process: "
                    "setting it after a worker has started takes effect only on that "
                    "worker's restart."
                ),
            )
        return AcceptanceVerdict(
            outcome="skipped",
            summary="No project environment is configured, so the application was not booted.",
            detail=(
                "Verification proved the recorded commands exited as expected against "
                "these commits. It did not prove the application runs. Configure a "
                "project environment to add that check."
            ),
        )
