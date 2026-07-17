"""
/api/runner — agent-runner configuration status.

`GET /runner/status` re-runs the non-raising checks from
`src/infra/runtime/factory.py` against the STORED config and agent registry,
plus the binary probes from `dependency_checker.py`, and always returns 200:
an invalid config is the query's answer, not an error. It never touches the
secret store (dry-run works without a master key) — secret existence/
decryption is still only checked when a task actually runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.api.security import require_api_token
from src.infra.container import AppContainer
from src.infra.runtime.dependency_checker import check_dependencies
from src.infra.runtime.factory import (
    validate_agent_binding,
    validate_agent_runner_mode,
)

router = APIRouter(
    prefix="/runner",
    dependencies=[Depends(require_api_token)],
    tags=["runner"],
)


class RunnerBinaryStatus(BaseModel):
    name: str
    binary: str
    ok: bool
    message: str
    install_hint: str | None = None
    is_runtime: bool


class RunnerAgentStatus(BaseModel):
    agent_id: str
    agent_name: str
    runtime_type: str
    valid: bool
    detail: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    model_name: str | None = None


class RunnerStatusResponse(BaseModel):
    mode: str
    valid: bool
    detail: str | None = None
    binaries: list[RunnerBinaryStatus]
    agents: list[RunnerAgentStatus]


@router.get("/status")
def runner_status(
    container: AppContainer = Depends(get_container),
) -> RunnerStatusResponse:
    mode_status = validate_agent_runner_mode(container.config_store)

    agents: list[RunnerAgentStatus] = []
    for spec in container.agent_repo.list():
        binding = validate_agent_binding(spec, container.provider_repo, container.model_repo)
        agents.append(
            RunnerAgentStatus(
                agent_id=spec.id,
                agent_name=spec.name,
                runtime_type=spec.runtime_type,
                valid=binding.valid,
                detail=binding.detail,
                provider_id=spec.provider_id,
                provider_name=binding.provider.name if binding.provider else None,
                model_id=spec.model_id,
                model_name=binding.model.name if binding.model else None,
            )
        )

    report = check_dependencies()
    binaries = [
        RunnerBinaryStatus(
            name=r.name,
            binary=r.binary,
            ok=r.ok,
            message=r.message,
            install_hint=r.install_hint or None,
            is_runtime=r.is_runtime,
        )
        for r in report.results
    ]

    # the global answer: mode must parse, and in real mode every registered
    # agent's binding must resolve (dry-run bypasses the registry entirely)
    valid = mode_status.valid
    detail = mode_status.detail
    if valid and mode_status.mode == "real":
        broken = next((a for a in agents if not a.valid), None)
        if broken is not None:
            valid = False
            detail = broken.detail
    return RunnerStatusResponse(
        mode=mode_status.mode,
        valid=valid,
        detail=detail,
        binaries=binaries,
        agents=agents,
    )
