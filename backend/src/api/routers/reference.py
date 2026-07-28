"""
/api/agents, /api/capabilities, /api/providers, /api/models, /api/projects —
reference-data CRUD over the SQLite catalog repositories (roadmap 2.6 #19).

Secrets rule: a provider is created/rotated with a plaintext key ONCE in the
request body; it goes straight into the envelope-encrypted secret store and
only the `api_key_ref` URI is ever stored or returned. No route echoes a key.

All routes are token-guarded, like every other router: the guard is applied once
at mount time in `server.py::create_app`, not declared here — see the comment
there. Open when no token is configured, i.e. local dev.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.factories.identity import new_id
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.container import AppContainer
from src.infra.db.secret_ref import SecretRef
from src.infra.errors import InfrastructureError, ProjectBindingInvalidError
from src.infra.git.repository_binding import validate_repo_url
from src.infra.runtime.factory import AGENT_RUNNER_CONFIG_INVALID, RUNTIME_TYPES

router = APIRouter(tags=["reference"])


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@router.get("/capabilities")
def list_capabilities(
    container: AppContainer = Depends(get_container),
) -> list[Capability]:
    return container.capability_repo.list()


@router.post("/capabilities", status_code=201)
def create_capability(
    body: Capability, container: AppContainer = Depends(get_container)
) -> Capability:
    container.capability_repo.add(body)
    return body


@router.put("/capabilities/{capability_id}", status_code=204)
def update_capability(
    capability_id: str,
    body: Capability,
    container: AppContainer = Depends(get_container),
) -> None:
    container.capability_repo.update(body.model_copy(update={"id": capability_id}))


@router.delete("/capabilities/{capability_id}", status_code=204)
def delete_capability(capability_id: str, container: AppContainer = Depends(get_container)) -> None:
    container.capability_repo.delete(capability_id)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentBody(BaseModel):
    name: str
    role: str
    model_role: str
    instructions: str = ""
    capability_ids: list[str] = []
    default_retry: RetryPolicy = RetryPolicy()
    runtime_type: str = "pi"  # pi | claude | gemini | dry-run
    provider_id: str | None = None
    model_id: str | None = None


def _to_spec(agent_id: str, body: AgentBody, container: AppContainer) -> AgentSpec:
    capabilities = [container.capability_repo.get(cid) for cid in body.capability_ids]
    # Referential write checks only: runtime_type must parse and any SUPPLIED
    # provider/model ref must resolve coherently. An agent may stay unbound
    # (create now, bind later) — /api/runner/status and the run-time TaskFailed
    # flag incomplete bindings.
    if body.runtime_type.strip().lower() not in RUNTIME_TYPES:
        raise InfrastructureError(
            f"runtime_type '{body.runtime_type}' — valid values are {', '.join(RUNTIME_TYPES)}.",
            code=AGENT_RUNNER_CONFIG_INVALID,
        )
    if body.provider_id:
        container.provider_repo.get(body.provider_id)  # typed 404 when ghost
    if body.model_id:
        model = container.model_repo.get(body.model_id)  # typed 404 when ghost
        if body.provider_id and model.provider_id != body.provider_id:
            raise InfrastructureError(
                f"model '{body.model_id}' belongs to provider "
                f"'{model.provider_id}', not '{body.provider_id}'.",
                code=AGENT_RUNNER_CONFIG_INVALID,
            )
    return AgentSpec(
        id=agent_id,
        name=body.name,
        role=body.role,
        model_role=body.model_role,
        instructions=body.instructions,
        capabilities=capabilities,
        default_retry=body.default_retry,
        runtime_type=body.runtime_type,
        provider_id=body.provider_id,
        model_id=body.model_id,
    )


class DefaultAgentResponse(BaseModel):
    agent_id: str | None


@router.get("/agents")
def list_agents(container: AppContainer = Depends(get_container)) -> list[AgentSpec]:
    return container.agent_repo.list()


@router.get("/agents/default")
def get_default_agent(
    container: AppContainer = Depends(get_container),
) -> DefaultAgentResponse:
    return DefaultAgentResponse(agent_id=container.agent_repo.get_default_id())


@router.post("/agents", status_code=201)
def create_agent(body: AgentBody, container: AppContainer = Depends(get_container)) -> AgentSpec:
    spec = _to_spec(new_id(), body, container)
    container.agent_repo.add(spec)
    return spec


@router.put("/agents/{agent_id}", status_code=204)
def update_agent(
    agent_id: str, body: AgentBody, container: AppContainer = Depends(get_container)
) -> None:
    """Granular agent editing (#18): the repo-level guard against editing an
    agent bound to a RUNNING task lands with the roadmap 3.5 mutation guards."""
    container.agent_repo.update(_to_spec(agent_id, body, container))


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str, container: AppContainer = Depends(get_container)) -> None:
    container.agent_repo.delete(agent_id)


@router.post("/agents/{agent_id}/default", status_code=204)
def set_default_agent(agent_id: str, container: AppContainer = Depends(get_container)) -> None:
    container.agent_repo.set_default(agent_id)


# ---------------------------------------------------------------------------
# Providers & models
# ---------------------------------------------------------------------------


class ProviderCreateBody(BaseModel):
    name: str
    base_url: str
    api_key: str  # accepted ONCE, stored encrypted, never echoed
    # Capacity metadata (domain unfreeze #16). Both optional: unset falls back to
    # the global execution.provider_max_inflight / per_model defaults.
    max_inflight: int | None = None
    capacity_scope: str | None = None


class ProviderUpdateBody(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None  # present = rotate the stored secret
    max_inflight: int | None = None
    capacity_scope: str | None = None


@router.get("/providers")
def list_providers(
    container: AppContainer = Depends(get_container),
) -> list[ModelProvider]:
    return container.provider_repo.list()  # carries api_key_ref, never the key


@router.post("/providers", status_code=201)
def create_provider(
    body: ProviderCreateBody, container: AppContainer = Depends(get_container)
) -> ModelProvider:
    provider_id = new_id()
    ref = SecretRef.for_provider(provider_id)
    container.secret_store.put(ref, body.api_key)
    provider = ModelProvider(
        id=provider_id,
        name=body.name,
        base_url=body.base_url,
        api_key_ref=ref.uri,
        max_inflight=body.max_inflight,
        capacity_scope=body.capacity_scope,
        models=[],
    )
    container.provider_repo.add(provider)
    return provider


@router.put("/providers/{provider_id}", status_code=204)
def update_provider(
    provider_id: str,
    body: ProviderUpdateBody,
    container: AppContainer = Depends(get_container),
) -> None:
    provider = container.provider_repo.get(provider_id)
    provider.name = body.name
    provider.base_url = body.base_url
    provider.max_inflight = body.max_inflight
    provider.capacity_scope = body.capacity_scope
    if body.api_key:
        container.secret_store.put(SecretRef(uri=provider.api_key_ref), body.api_key)
    container.provider_repo.update(provider)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, container: AppContainer = Depends(get_container)) -> None:
    provider = container.provider_repo.get(provider_id)
    container.provider_repo.delete(provider_id)
    container.secret_store.delete(SecretRef(uri=provider.api_key_ref))


class ModelBody(BaseModel):
    name: str
    # Optional per-model override of the provider's in-flight cap (unfreeze #16).
    max_inflight: int | None = None


@router.get("/models")
def list_models(container: AppContainer = Depends(get_container)) -> list[IAModel]:
    return container.model_repo.list()


@router.post("/providers/{provider_id}/models", status_code=201)
def create_model(
    provider_id: str, body: ModelBody, container: AppContainer = Depends(get_container)
) -> IAModel:
    model = IAModel(
        id=new_id(),
        provider_id=provider_id,
        name=body.name,
        max_inflight=body.max_inflight,
    )
    container.model_repo.add(model)
    return model


@router.put("/models/{model_id}", status_code=204)
def update_model(
    model_id: str, body: ModelBody, container: AppContainer = Depends(get_container)
) -> None:
    """Rename + capacity override only — a model's provider binding is immutable."""
    model = container.model_repo.get(model_id)
    container.model_repo.update(
        model.model_copy(update={"name": body.name, "max_inflight": body.max_inflight})
    )


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str, container: AppContainer = Depends(get_container)) -> None:
    container.model_repo.delete(model_id)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectBody(BaseModel):
    name: str
    repo_url: str | None = None


@router.get("/projects")
def list_projects(
    container: AppContainer = Depends(get_container),
) -> list[ProjectDefinition]:
    return container.project_repo.list()


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectBody, container: AppContainer = Depends(get_container)
) -> ProjectDefinition:
    validate_repo_url(body.repo_url)
    project = ProjectDefinition(id=new_id(), name=body.name, repo_url=body.repo_url)
    container.project_repo.add(project)
    return project


@router.put("/projects/{project_id}", status_code=204)
def update_project(
    project_id: str, body: ProjectBody, container: AppContainer = Depends(get_container)
) -> None:
    validate_repo_url(body.repo_url)
    container.project_repo.update(
        ProjectDefinition(id=project_id, name=body.name, repo_url=body.repo_url)
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, container: AppContainer = Depends(get_container)) -> None:
    container.project_repo.delete(project_id)


class ProjectReadinessResponse(BaseModel):
    binding: str
    repo_url: str | None
    resolved_path: str | None
    exists: bool
    is_git_repository: bool
    default_branch: str | None
    # Informational, never a readiness verdict: attempts branch from a COMMITTED
    # ref, so uncommitted changes in the target's main worktree do not affect a
    # run. An operator about to start a 25-minute cycle still wants to know.
    # None when the path is not a repository at all.
    clean: bool | None
    problem: str | None


@router.get("/projects/{project_id}/readiness", response_model=ProjectReadinessResponse)
def project_readiness(
    project_id: str, container: AppContainer = Depends(get_container)
) -> ProjectReadinessResponse:
    """Diagnose one project's repository without running a plan against it.

    Write-time validation (`validate_repo_url`, called from create/update
    above) cannot cover a path that disappears afterwards, so this re-checks
    the live filesystem on every call. The scratch/local/remote path itself is
    NOT re-derived here — `ProjectWorkspaceResolver.repository_path_for` is the
    one place that rule lives.
    """
    project = container.project_repo.get(project_id)
    resolved_path = container.workspace_resolver.repository_path_for(project)

    try:
        binding = validate_repo_url(project.repo_url)
        kind = binding.kind
        default_branch = binding.default_branch
        problem: str | None = None
    except ProjectBindingInvalidError as exc:
        # A readiness probe that raises is useless. validate_repo_url only
        # ever raises for a local-looking repo_url (scratch and remote never
        # do), so catching exactly its declared error type here — the one
        # broad-looking catch that is correct — turns the failure into data.
        kind = "local"
        default_branch = None
        problem = exc.message

    is_repository = (resolved_path / ".git").exists()
    return ProjectReadinessResponse(
        binding=kind,
        repo_url=project.repo_url,
        resolved_path=str(resolved_path),
        exists=resolved_path.exists(),
        is_git_repository=is_repository,
        default_branch=default_branch,
        clean=_worktree_is_clean(resolved_path) if is_repository else None,
        problem=problem,
    )


def _worktree_is_clean(repo: Path) -> bool | None:
    """None when git itself cannot answer — an unreadable repo is not "dirty"."""
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() == ""
