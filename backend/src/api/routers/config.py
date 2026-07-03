"""
/api/config — the two-tier config store (roadmap 2.8): scope 'orchestrator'
for machine settings, a project id for per-project settings (incl. the
framework/dev-tool questionnaire fields; the env provisioner that consumes
them is deferred). Token-guarded like the rest of the control plane.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.api.security import require_api_token
from src.infra.container import AppContainer

router = APIRouter(
    prefix="/config", dependencies=[Depends(require_api_token)], tags=["config"]
)


class ConfigValue(BaseModel):
    value: str


@router.get("/{scope}")
def get_scope(scope: str, container: AppContainer = Depends(get_container)) -> dict:
    return container.config_store.all(scope)


@router.put("/{scope}/{key}", status_code=204)
def set_value(
    scope: str,
    key: str,
    body: ConfigValue,
    container: AppContainer = Depends(get_container),
) -> None:
    container.config_store.set(scope, key, body.value)


@router.delete("/{scope}/{key}", status_code=204)
def delete_value(
    scope: str, key: str, container: AppContainer = Depends(get_container)
) -> None:
    container.config_store.delete(scope, key)
