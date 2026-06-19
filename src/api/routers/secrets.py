"""
src/api/routers/secrets.py — secret control plane (write-only + masked read).

Secrets are write-only: POST stores ciphertext; GET returns only the known refs
and whether each is set. Resolved plaintext never crosses this boundary.

Refs are enumerated from the config store (each provider's key + each project's
GitHub token) so we never need a list-plaintext capability on the secret store.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from src.api.dependencies import ConfigStoreDep, SecretStoreDep
from src.api.schemas.control import SecretCreateRequest, SecretRefResponse
from src.api.security import require_api_token
from src.domain.value_objects.config import SecretRef

if TYPE_CHECKING:
    from src.domain.repositories.config_store import ConfigStorePort
    from src.domain.repositories.secret_store import SecretStorePort

router = APIRouter(
    prefix="/secrets",
    tags=["secrets"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[SecretRefResponse], summary="List Secret Refs")
def list_secret_refs(config: ConfigStoreDep, secrets: SecretStoreDep) -> list[SecretRefResponse]:
    cfg: "ConfigStorePort" = config  # type: ignore[assignment]
    store: "SecretStorePort" = secrets  # type: ignore[assignment]
    refs: list[SecretRef] = [p.secret_ref for p in cfg.list_providers()]
    refs += [
        proj.github_secret_ref
        for proj in cfg.list_projects()
        if proj.github_secret_ref is not None
    ]
    return [
        SecretRefResponse(uri=ref.uri, is_set=store.exists(ref))
        for ref in refs
    ]


@router.post(
    "",
    response_model=SecretRefResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store Secret",
)
def store_secret(body: SecretCreateRequest, secrets: SecretStoreDep) -> SecretRefResponse:
    store: "SecretStorePort" = secrets  # type: ignore[assignment]
    ref = SecretRef(uri=body.uri)
    store.put(ref, body.value)
    return SecretRefResponse(uri=ref.uri, is_set=True)
