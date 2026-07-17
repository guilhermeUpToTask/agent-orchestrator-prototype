"""
src/infra/runtime/factory.py — build the AgentRunner from the agent registry
and the providers catalog (mirrors src/infra/reasoner/factory.py).

Two global modes, selected by the config key `agent_runner.mode`
(scope 'orchestrator'):

  dry-run (default) — every task runs on the DummyAgentRunner (same
                      FailureKind taxonomy as the real CLI runners). NEVER
                      touches the secret store, so dry-run works without
                      ORCHESTRATOR_MASTER_KEY.
  real              — the CatalogAgentRunner: each task resolves through the
                      AGENT REGISTRY — the bound AgentSpec's `runtime_type`
                      (pi default | claude | gemini | dry-run) picks the CLI
                      runtime, and its `provider_id`/`model_id` rows supply
                      the credentials (api_key_ref -> secret store) and the
                      provider model string. Resolution happens per run, so
                      agent edits and key rotations apply without a restart.

Config keys (scope 'orchestrator'):
  agent_runner.mode             dry-run | real              (default dry-run)
  agent_runner.timeout_seconds  int, per-attempt subprocess (default 600)

For the pi runtime the provider row must also map to a pi backend (which env
var pi hands the key through): the provider id — falling back to its name —
is matched case-insensitively against PI_BACKEND_ENV_VAR.

`validate_agent_runner_mode` / `validate_agent_binding` are the non-raising
checks shared by `build_agent_runner`, the per-run resolution, and the
`/api/runner/status` endpoint. They cover catalog wiring only — secret
existence/decryption is still checked at run time, because checking it here
would require the master key (which dry-run does not have).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import structlog

from src.app.ports import AgentEventSink, TaskFailed, WorkspaceHandle
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.task import Task
from src.domain.errors.config_errors import (
    ModelNotFoundError,
    ModelProviderNotFoundError,
)
from src.domain.ports.agent_port import AgentRunner
from src.domain.value_objects.lifecycle import FailureKind
from src.domain.value_objects.tasks_vos import TaskResult
from src.infra.db.reference_repos import (
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
)
from src.infra.db.secret_ref import SecretRef
from src.infra.db.secret_store import SqliteSecretStore
from src.infra.errors import InfrastructureError
from src.infra.runtime.cli_runner import (
    PI_BACKEND_ENV_VAR,
    ClaudeCodeRunner,
    GeminiRunner,
    PiAgentRunner,
)
from src.infra.runtime.dummy_runner import DryRunAgentRunner

log = structlog.get_logger(__name__)

AGENT_RUNNER_CONFIG_INVALID = "AGENT_RUNNER_CONFIG_INVALID"

RUNTIME_TYPES = ("pi", "claude", "gemini", "dry-run")

_SCOPE = SqliteConfigStore.ORCHESTRATOR_SCOPE
_DEFAULT_TIMEOUT_SECONDS = 600


def _invalid(message: str) -> InfrastructureError:
    return InfrastructureError(message, code=AGENT_RUNNER_CONFIG_INVALID)


@dataclass(frozen=True)
class RunnerModeStatus:
    """The global agent_runner.mode check — never raises."""

    mode: str
    valid: bool
    detail: str | None = None


@dataclass(frozen=True)
class AgentBindingStatus:
    """One agent's runtime wiring check — never raises, never touches
    secrets. `detail` carries the exact resolution-failure message."""

    valid: bool
    detail: str | None = None
    provider: ModelProvider | None = None
    model: IAModel | None = None


def validate_agent_runner_mode(config_store: SqliteConfigStore) -> RunnerModeStatus:
    mode = (config_store.get(_SCOPE, "agent_runner.mode") or "dry-run").strip().lower()
    if mode in ("dry-run", "real"):
        return RunnerModeStatus(mode=mode, valid=True)
    return RunnerModeStatus(
        mode=mode,
        valid=False,
        detail=(
            f"agent_runner.mode is '{mode}' — valid values are 'dry-run' or "
            "'real'. Fix it with: "
            "orchestrate config set agent_runner.mode dry-run|real"
        ),
    )


def _pi_backend_for(provider: ModelProvider) -> str | None:
    """Map a provider row to the pi backend name (the env var pi reads)."""
    for candidate in (provider.id.lower(), provider.name.lower()):
        if candidate in PI_BACKEND_ENV_VAR:
            return candidate
    return None


def validate_agent_binding(
    spec: AgentSpec,
    provider_repo: SqliteModelProviderRepository,
    model_repo: SqliteModelRepository,
) -> AgentBindingStatus:
    runtime = spec.runtime_type.strip().lower()
    if runtime not in RUNTIME_TYPES:
        return AgentBindingStatus(
            valid=False,
            detail=(
                f"agent '{spec.id}' has runtime_type '{spec.runtime_type}' — "
                f"valid values are {', '.join(RUNTIME_TYPES)}."
            ),
        )
    if runtime == "dry-run":
        return AgentBindingStatus(valid=True)

    if not spec.provider_id:
        return AgentBindingStatus(
            valid=False,
            detail=(
                f"agent '{spec.id}' uses the '{runtime}' runtime but has no "
                "provider_id — bind it to a provider from the catalog."
            ),
        )
    if not spec.model_id:
        return AgentBindingStatus(
            valid=False,
            detail=(
                f"agent '{spec.id}' uses the '{runtime}' runtime but has no "
                "model_id — bind it to a model from the catalog."
            ),
        )
    try:
        provider = provider_repo.get(spec.provider_id)
    except ModelProviderNotFoundError:
        return AgentBindingStatus(
            valid=False,
            detail=(
                f"agent '{spec.id}' points at provider '{spec.provider_id}', "
                "which does not exist in the providers catalog."
            ),
        )
    try:
        model = model_repo.get(spec.model_id)
    except ModelNotFoundError:
        return AgentBindingStatus(
            valid=False,
            provider=provider,
            detail=(
                f"agent '{spec.id}' points at model '{spec.model_id}', which "
                "does not exist in the models catalog."
            ),
        )
    if model.provider_id != provider.id:
        return AgentBindingStatus(
            valid=False,
            provider=provider,
            model=model,
            detail=(
                f"agent '{spec.id}': model '{spec.model_id}' belongs to "
                f"provider '{model.provider_id}', not the bound provider "
                f"'{spec.provider_id}'."
            ),
        )
    if runtime == "pi" and _pi_backend_for(provider) is None:
        return AgentBindingStatus(
            valid=False,
            provider=provider,
            model=model,
            detail=(
                f"agent '{spec.id}': provider '{spec.provider_id}' does not "
                "map to a pi backend — the pi runtime supports providers "
                f"named {', '.join(sorted(PI_BACKEND_ENV_VAR))}."
            ),
        )
    return AgentBindingStatus(valid=True, provider=provider, model=model)


class CatalogAgentRunner:
    """agent_runner.mode=real: dispatch each run to the CLI runtime the bound
    AgentSpec names, with credentials/model resolved through the catalog.

    Resolution is per run — agents are edited and keys rotated at runtime, so
    caching a built runner would pin stale credentials until a restart. A
    broken binding raises TaskFailed(auth_error): terminal, config never
    fixes itself mid-retry-loop."""

    def __init__(
        self,
        config_store: SqliteConfigStore,
        provider_repo: SqliteModelProviderRepository,
        model_repo: SqliteModelRepository,
        secret_store: Callable[[], SqliteSecretStore],
    ) -> None:
        self._config_store = config_store
        self._provider_repo = provider_repo
        self._model_repo = model_repo
        self._secret_store = secret_store
        self._dummy = DryRunAgentRunner()

    def _timeout_seconds(self) -> int:
        raw = self._config_store.get(_SCOPE, "agent_runner.timeout_seconds")
        return int(raw) if raw else _DEFAULT_TIMEOUT_SECONDS

    def _runner_for(self, spec: AgentSpec) -> AgentRunner:
        binding = validate_agent_binding(spec, self._provider_repo, self._model_repo)
        if not binding.valid:
            raise TaskFailed(
                binding.detail or f"agent '{spec.id}' runtime binding is invalid",
                FailureKind.AUTH_ERROR,
            )
        runtime = spec.runtime_type.strip().lower()
        if runtime == "dry-run":
            return self._dummy

        provider = binding.provider
        model = binding.model
        assert provider is not None and model is not None  # valid binding carries both
        api_key = self._secret_store().resolve_plaintext(SecretRef(uri=provider.api_key_ref))
        timeout = self._timeout_seconds()
        if runtime == "pi":
            backend = _pi_backend_for(provider)
            assert backend is not None  # validated in the binding check
            return PiAgentRunner(
                api_key=api_key,
                model=model.name,
                backend=backend,
                timeout_seconds=timeout,
                provider_id=provider.id,
                model_id=model.id,
            )
        if runtime == "claude":
            return ClaudeCodeRunner(
                api_key=api_key,
                model=model.name,
                timeout_seconds=timeout,
                provider_id=provider.id,
                model_id=model.id,
            )
        return GeminiRunner(
            api_key=api_key,
            model=model.name,
            timeout_seconds=timeout,
            provider_id=provider.id,
            model_id=model.id,
        )

    async def run(
        self,
        task: Task,
        spec: AgentSpec,
        *,
        idempotency_key: str,
        event_sink: AgentEventSink,
        workspace: WorkspaceHandle,
    ) -> TaskResult:
        runner = self._runner_for(spec)
        log.info(
            "agent_runner.resolved",
            agent_id=spec.id,
            runtime_type=spec.runtime_type,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
        )
        try:
            return await runner.run(
                task,
                spec,
                idempotency_key=idempotency_key,
                event_sink=event_sink,
                workspace=workspace,
            )
        except TaskFailed as exc:
            failure = exc.failure.with_identity(
                runtime=spec.runtime_type,
                provider_id=spec.provider_id,
                model_id=spec.model_id,
            )
            raise TaskFailed(failure.safe_message, failure=failure) from exc


def build_agent_runner(
    config_store: SqliteConfigStore,
    provider_repo: SqliteModelProviderRepository,
    model_repo: SqliteModelRepository,
    secret_store: Callable[[], SqliteSecretStore],
) -> AgentRunner:
    """`secret_store` is a thunk: dry-run mode must never construct it (it
    fails closed on a missing master key, which dry-run does not have)."""
    status = validate_agent_runner_mode(config_store)
    if not status.valid:
        raise _invalid(status.detail or "agent_runner.mode is invalid")
    if status.mode == "dry-run":
        return DryRunAgentRunner()
    return CatalogAgentRunner(config_store, provider_repo, model_repo, secret_store)
