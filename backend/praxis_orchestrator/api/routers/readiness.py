"""One call that answers "can this machine run a plan?".

Composed from the validators that already serve /api/reasoner/status
(`validate_reasoner_config`) and /api/runner/status (`validate_agent_runner_mode`,
`validate_agent_binding`, `check_dependencies`) — it reimplements none of them.
`warn` exists so a missing optional runtime binary does not read as a broken
install. No check ever returns secret material: the secrets check reports the
presence of the master key and nothing else, and the catalog/reasoner checks
carry provider/model NAMES (already public via /api/providers) rather than
keys.

Every check function is written so it cannot itself raise. Most of the
validators above already guarantee that (see their own docstrings). The one
exception is `validate_repo_url` (Task 4), which raises `ProjectBindingInvalidError`
for a broken local binding — write-time validation cannot catch a directory
deleted afterwards. A readiness probe that raises defeats its own purpose, so
`_projects` below wraps that one call in a try/except scoped to exactly that
declared error type. This is the one place in this router a catch like that is
correct.
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.api.routers.workers import STALE_AFTER_SECONDS
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.secret_store import MASTER_KEY_ENV
from praxis_orchestrator.infra.errors import ProjectBindingInvalidError
from praxis_orchestrator.infra.git.repository_binding import validate_repo_url
from praxis_orchestrator.infra.reasoner.factory import ReasonerConfigStatus, validate_reasoner_config
from praxis_orchestrator.infra.runtime.dependency_checker import check_dependencies
from praxis_orchestrator.infra.runtime.factory import (
    RunnerModeStatus,
    validate_agent_binding,
    validate_agent_runner_mode,
)

router = APIRouter(tags=["readiness"])

Status = Literal["ok", "warn", "fail"]


class ReadinessCheck(BaseModel):
    name: str
    status: Status
    detail: str


class ReadinessResponse(BaseModel):
    ok: bool
    checks: list[ReadinessCheck]


@router.get("/readiness", response_model=ReadinessResponse)
def readiness(container: AppContainer = Depends(get_container)) -> ReadinessResponse:
    reasoner_status = validate_reasoner_config(
        container.config_store, container.provider_repo, container.model_repo
    )
    runner_status = validate_agent_runner_mode(container.config_store)
    checks = [
        _reasoner(reasoner_status),
        _runner(runner_status, container),
        _binaries(),
        _secrets(reasoner_status.mode, runner_status.mode),
        _catalog(container),
        _projects(container),
        _workers(container),
    ]
    return ReadinessResponse(ok=all(check.status != "fail" for check in checks), checks=checks)


def _reasoner(status: ReasonerConfigStatus) -> ReadinessCheck:
    if not status.valid:
        return ReadinessCheck(
            name="reasoner", status="fail", detail=status.detail or "invalid reasoner configuration"
        )
    if status.mode == "stub":
        return ReadinessCheck(name="reasoner", status="ok", detail="stub")
    provider_name = status.provider.name if status.provider else "?"
    model_name = status.model.name if status.model else "?"
    return ReadinessCheck(
        name="reasoner", status="ok", detail=f"{status.mode} · {provider_name} · {model_name}"
    )


def _runner(mode_status: RunnerModeStatus, container: AppContainer) -> ReadinessCheck:
    if not mode_status.valid:
        return ReadinessCheck(
            name="runner", status="fail", detail=mode_status.detail or "invalid agent_runner configuration"
        )
    if mode_status.mode != "real":
        return ReadinessCheck(name="runner", status="ok", detail=mode_status.mode)

    agents = container.agent_repo.list()
    bindings = [
        validate_agent_binding(spec, container.provider_repo, container.model_repo) for spec in agents
    ]
    broken = next((binding for binding in bindings if not binding.valid), None)
    if broken is not None:
        return ReadinessCheck(name="runner", status="fail", detail=broken.detail or "an agent binding is invalid")
    return ReadinessCheck(
        name="runner", status="ok", detail=f"real · {len(agents)} agent(s), all bindings resolve"
    )


def _binaries() -> ReadinessCheck:
    report = check_dependencies()
    failing = report.failing()
    if not failing:
        return ReadinessCheck(name="binaries", status="ok", detail="all probes ok")
    detail = ", ".join(f"{result.name} not on PATH" for result in failing)
    return ReadinessCheck(name="binaries", status="warn", detail=detail)


def _secrets(reasoner_mode: str, runner_mode: str) -> ReadinessCheck:
    """Presence only — never the key, never a fingerprint of it.

    The master key is needed only when something actually decrypts a provider
    key. Tier 0 (`reasoner.mode=stub` + `agent_runner.mode=dry-run`) never
    touches the secret store, so reporting `fail` there would tell an operator
    their working free-tier install is broken.
    """
    if os.environ.get(MASTER_KEY_ENV, "").strip():
        return ReadinessCheck(name="secrets", status="ok", detail="master key present")
    needed_by = [
        label
        for label, mode, live in (
            ("reasoner", reasoner_mode, "llm"),
            ("agent runner", runner_mode, "real"),
        )
        if mode == live
    ]
    if not needed_by:
        return ReadinessCheck(
            name="secrets",
            status="ok",
            detail=f"{MASTER_KEY_ENV} not set, and not needed in stub/dry-run",
        )
    return ReadinessCheck(
        name="secrets",
        status="fail",
        detail=f"{MASTER_KEY_ENV} is not set, and {' and '.join(needed_by)} must decrypt a provider key",
    )


def _catalog(container: AppContainer) -> ReadinessCheck:
    capabilities = container.capability_repo.list()
    agents = container.agent_repo.list()
    providers = container.provider_repo.list()
    models = container.model_repo.list()
    status: Status = "ok" if agents and providers and models else "fail"
    detail = f"{len(capabilities)} capabilities · {len(agents)} agents · {len(models)} provider/model"
    return ReadinessCheck(name="catalog", status=status, detail=detail)


def _workers(container: AppContainer) -> ReadinessCheck:
    """`fail` for never-started, `warn` for went-quiet.

    They are different mistakes. No row at all means the operator has not run
    `praxis worker start` — nothing will ever pick up a plan, and the
    checklist should say so plainly. A row that has gone stale usually means a
    restart in progress, which resolves itself; calling that a failure would
    train an operator to ignore this check.
    """
    now = container.clock.now()
    rows = container.worker_registry.list_workers()
    if not rows:
        return ReadinessCheck(
            name="workers",
            status="fail",
            detail="no worker has reported — start one with `praxis worker start`",
        )
    ages = [(now - row.last_seen_at).total_seconds() for row in rows]
    fresh = [age for age in ages if age <= STALE_AFTER_SECONDS]
    if not fresh:
        return ReadinessCheck(
            name="workers",
            status="warn",
            detail=f"{len(rows)} worker(s) known, none seen in the last {min(ages):.0f}s",
        )
    return ReadinessCheck(
        name="workers", status="ok", detail=f"{len(fresh)} of {len(rows)} worker(s) live"
    )


def _projects(container: AppContainer) -> ReadinessCheck:
    projects = container.project_repo.list()
    if not projects:
        return ReadinessCheck(name="projects", status="ok", detail="no projects yet")
    broken = 0
    for project in projects:
        try:
            validate_repo_url(project.repo_url)
        except ProjectBindingInvalidError:
            # See module docstring: the one validator this router calls that
            # can raise, caught for exactly its declared error type.
            broken += 1
    if broken:
        return ReadinessCheck(
            name="projects",
            status="fail",
            detail=f"{broken} of {len(projects)} projects have an unusable repository",
        )
    return ReadinessCheck(name="projects", status="ok", detail=f"{len(projects)} projects ok")
