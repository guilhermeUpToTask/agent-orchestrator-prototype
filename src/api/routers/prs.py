"""
src/api/routers/prs.py — PR-window endpoints.

Thin over GitForgePort (GitHub primary, local-git fallback). Commit graph is
topology only — layout is computed client-side. PR/review/check data comes from
GitHub; local git advertises its gaps via capabilities and returns empty lists.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.api.dependencies import GitForgeDep
from src.api.schemas.forge import (
    CommitGraphResponse,
    ForgeCapabilitiesResponse,
    PullRequestResponse,
)
from src.api.security import require_api_token
from src.domain.value_objects.forge import PrState

if TYPE_CHECKING:
    from src.domain.ports.forge import GitForgePort

router = APIRouter(tags=["pull-requests"], dependencies=[Depends(require_api_token)])


@router.get(
    "/projects/{project_id}/commit-graph",
    response_model=CommitGraphResponse,
    summary="Commit Graph",
)
def commit_graph(
    project_id: str,
    forge: GitForgeDep,
    branch: str | None = None,
    limit: int = 200,
) -> CommitGraphResponse:
    f: "GitForgePort" = forge  # type: ignore[assignment]
    return CommitGraphResponse.from_domain(f.commit_graph(branch=branch, limit=limit))


@router.get(
    "/projects/{project_id}/forge-capabilities",
    response_model=ForgeCapabilitiesResponse,
    summary="Forge Capabilities",
)
def forge_capabilities(project_id: str, forge: GitForgeDep) -> ForgeCapabilitiesResponse:
    f: "GitForgePort" = forge  # type: ignore[assignment]
    return ForgeCapabilitiesResponse.from_domain(f.capabilities())


@router.get(
    "/projects/{project_id}/prs",
    response_model=list[PullRequestResponse],
    summary="List Pull Requests",
)
def list_prs(
    project_id: str,
    forge: GitForgeDep,
    state: PrState | None = None,
) -> list[PullRequestResponse]:
    f: "GitForgePort" = forge  # type: ignore[assignment]
    return [PullRequestResponse.from_domain(p) for p in f.list_prs(state=state)]


@router.get(
    "/prs/pending-reviews",
    response_model=list[PullRequestResponse],
    summary="Pending Reviews",
)
def pending_reviews(forge: GitForgeDep, reviewer: str) -> list[PullRequestResponse]:
    f: "GitForgePort" = forge  # type: ignore[assignment]
    return [PullRequestResponse.from_domain(p) for p in f.pending_reviews(reviewer=reviewer)]


@router.get(
    "/prs/{number}",
    response_model=PullRequestResponse,
    summary="Get Pull Request",
)
def get_pr(number: int, forge: GitForgeDep) -> PullRequestResponse:
    f: "GitForgePort" = forge  # type: ignore[assignment]
    return PullRequestResponse.from_domain(f.get_pr(number))
