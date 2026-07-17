"""
/api/reasoner — reasoner configuration status.

`GET /reasoner/status` re-runs the catalog-wiring validation from
`src/infra/reasoner/factory.py` against the STORED config and always returns
200: an invalid config is the query's answer, not an error. It never touches
the secret store (stub/dry-run works without a master key) — secret
existence/decryption is still only checked when the reasoner is built at
process start.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.api.security import require_api_token
from src.infra.container import AppContainer
from src.infra.reasoner.factory import validate_reasoner_config

router = APIRouter(
    prefix="/reasoner",
    dependencies=[Depends(require_api_token)],
    tags=["reasoner"],
)


class ReasonerStatusResponse(BaseModel):
    mode: str
    valid: bool
    detail: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    model_name: str | None = None


@router.get("/status")
def reasoner_status(
    container: AppContainer = Depends(get_container),
) -> ReasonerStatusResponse:
    status = validate_reasoner_config(
        container.config_store, container.provider_repo, container.model_repo
    )
    return ReasonerStatusResponse(
        mode=status.mode,
        valid=status.valid,
        detail=status.detail,
        provider_id=status.provider.id if status.provider else None,
        provider_name=status.provider.name if status.provider else None,
        model_id=status.model.id if status.model else None,
        model_name=status.model.name if status.model else None,
    )
