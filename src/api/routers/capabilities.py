"""
src/api/routers/capabilities.py — Capability registry endpoints.

The capability registry is the dynamic source of truth for which tags agents
and tasks may use. Tags are seeded with built-in defaults and extended here.

  GET    /capabilities          list all registered tags
  POST   /capabilities          register a new tag
  DELETE /capabilities/{tag}     remove a tag
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, status

from src.api.dependencies import CapabilityRegistryDep
from src.api.schemas.capabilities import CapabilityCreateRequest, CapabilityListResponse

if TYPE_CHECKING:
    from src.domain import CapabilityRegistryPort

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get(
    "",
    response_model=CapabilityListResponse,
    summary="List Capability Tags",
    description="Returns all capability tags registered for this project.",
)
def list_capabilities(registry: CapabilityRegistryDep) -> CapabilityListResponse:
    reg: "CapabilityRegistryPort" = registry  # type: ignore[assignment]
    return CapabilityListResponse(tags=reg.list_tags())


@router.post(
    "",
    response_model=CapabilityListResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Capability Tag",
    description=(
        "Registers a new capability tag (normalized; a malformed tag is "
        "rejected). Idempotent — registering an existing tag is a no-op."
    ),
)
def create_capability(
    payload: CapabilityCreateRequest, registry: CapabilityRegistryDep
) -> CapabilityListResponse:
    reg: "CapabilityRegistryPort" = registry  # type: ignore[assignment]
    reg.add(payload.tag)  # ValueError on malformed tag → 409 via global handler
    return CapabilityListResponse(tags=reg.list_tags())


@router.delete(
    "/{tag}",
    response_model=CapabilityListResponse,
    summary="Remove Capability Tag",
    description="Removes a capability tag. No-op if the tag is not registered.",
)
def delete_capability(tag: str, registry: CapabilityRegistryDep) -> CapabilityListResponse:
    reg: "CapabilityRegistryPort" = registry  # type: ignore[assignment]
    reg.remove(tag)
    return CapabilityListResponse(tags=reg.list_tags())
