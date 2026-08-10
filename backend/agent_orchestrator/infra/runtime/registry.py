"""The one place a CLI agent runtime is described.

Adding `codex` on 2026-08-09 took coordinated edits in FOUR files — the runner
class, the `RUNTIME_TYPES` tuple, the factory's dispatch branch, and the
dependency probe's table — and **nothing failed if one was forgotten**. The
runtime just behaved oddly in whichever surface was missed: registerable but not
dispatchable, or installed-looking but unprobed. That is the OCP smell this
module removes.

A runtime is now one `RuntimeDescriptor`. `RUNTIME_TYPES`, the probe table and
the factory's dispatch all read from here, and `test_runtime_registry.py` fails
if they can disagree.

**What is deliberately NOT abstracted:** codex needs no API key because it
authenticates from `CODEX_HOME`, while its siblings are handed an
envelope-encrypted secret. That asymmetry is a real property of the runtime, so
it is a declared field (`needs_api_key`) rather than something hidden behind a
default — the factory reads it to decide whether to touch the secret store at
all, and a runtime that needs no secret must not look like one that lost its
secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_orchestrator.app.observations import ObservationRepository
from agent_orchestrator.app.ports import PriorAttemptFeedback, Sandbox
from agent_orchestrator.domain.entities.ia_model import IAModel
from agent_orchestrator.domain.entities.model_provider import ModelProvider
from agent_orchestrator.domain.ports.agent_port import AgentRunner
from agent_orchestrator.infra.runtime.cli_runner import (
    PI_BACKEND_ENV_VAR,
    ClaudeCodeRunner,
    CodexRunner,
    GeminiRunner,
    PiAgentRunner,
)


@dataclass(frozen=True)
class RuntimeBuild:
    """Everything a runtime needs to be constructed for ONE task run.

    A parameter object rather than a long argument list: the factory used to
    thread these individually through a four-branch if-chain, so each new
    runtime repeated the same nine arguments.
    """

    provider: ModelProvider
    model: IAModel
    api_key: str | None
    timeout_seconds: int
    orchestrator_home: Path | None
    observation_repository: ObservationRepository | None
    sandbox: Sandbox | None
    prior_attempt_feedback: PriorAttemptFeedback | None

    def shared(self) -> dict[str, object]:
        """The arguments every `CliAgentRunner` takes, spelled once."""
        return {
            "timeout_seconds": self.timeout_seconds,
            "provider_id": self.provider.id,
            "model_id": self.model.id,
            "orchestrator_home": self.orchestrator_home,
            "observation_repository": self.observation_repository,
            "sandbox": self.sandbox,
            "prior_attempt_feedback": self.prior_attempt_feedback,
        }


@dataclass(frozen=True)
class RuntimeDescriptor:
    """One CLI runtime, described once.

    `binary` is None for a runtime that shells out to nothing (`dry-run`), which
    is also why it is the only one the dependency probe skips.
    """

    name: str
    binary: str | None
    install_hint: str
    needs_api_key: bool
    build: Callable[[RuntimeBuild], AgentRunner] | None = None

    def pi_backend(self, provider: ModelProvider) -> str | None:
        """Only meaningful for pi: which env var it hands the key through."""
        if self.name != "pi":
            return None
        for candidate in (provider.id.lower(), provider.name.lower()):
            if candidate in PI_BACKEND_ENV_VAR:
                return candidate
        return None


def _build_pi(ctx: RuntimeBuild) -> AgentRunner:
    backend = RUNTIME_REGISTRY["pi"].pi_backend(ctx.provider)
    assert backend is not None  # validated in the binding check
    assert ctx.api_key is not None
    return PiAgentRunner(api_key=ctx.api_key, model=ctx.model.name, backend=backend, **ctx.shared())  # type: ignore[arg-type]


def _build_claude(ctx: RuntimeBuild) -> AgentRunner:
    assert ctx.api_key is not None
    return ClaudeCodeRunner(api_key=ctx.api_key, model=ctx.model.name, **ctx.shared())  # type: ignore[arg-type]


def _build_gemini(ctx: RuntimeBuild) -> AgentRunner:
    assert ctx.api_key is not None
    return GeminiRunner(api_key=ctx.api_key, model=ctx.model.name, **ctx.shared())  # type: ignore[arg-type]


def _build_codex(ctx: RuntimeBuild) -> AgentRunner:
    # No api_key: see the module docstring.
    return CodexRunner(model=ctx.model.name, **ctx.shared())  # type: ignore[arg-type]


RUNTIME_REGISTRY: dict[str, RuntimeDescriptor] = {
    descriptor.name: descriptor
    for descriptor in (
        RuntimeDescriptor(
            name="pi",
            binary="pi",
            install_hint="See pi-mono installation docs — build from source or npm",
            needs_api_key=True,
            build=_build_pi,
        ),
        RuntimeDescriptor(
            name="claude",
            binary="claude",
            install_hint="npm install -g @anthropic-ai/claude-code",
            needs_api_key=True,
            build=_build_claude,
        ),
        RuntimeDescriptor(
            name="codex",
            binary="codex",
            # Installing codex is only half of it: the runtime reads a
            # subscription credential from CODEX_HOME, so `codex login` has to
            # have been run too. The probe can only see the binary.
            install_hint="npm install -g @openai/codex, then run: codex login",
            needs_api_key=False,
            build=_build_codex,
        ),
        RuntimeDescriptor(
            name="gemini",
            binary="gemini",
            install_hint="npm install -g @google/gemini-cli",
            needs_api_key=True,
            build=_build_gemini,
        ),
        # Shells out to nothing and must NEVER reach the secret store — dry-run
        # has to work without ORCHESTRATOR_MASTER_KEY. The factory returns its
        # shared dummy instance, so there is no builder here.
        RuntimeDescriptor(
            name="dry-run",
            binary=None,
            install_hint="",
            needs_api_key=False,
            build=None,
        ),
    )
}


def runtime_names() -> tuple[str, ...]:
    """Every registered runtime name, in registration order."""
    return tuple(RUNTIME_REGISTRY)
