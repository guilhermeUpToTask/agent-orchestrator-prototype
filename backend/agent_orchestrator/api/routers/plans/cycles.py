"""The cycle gates: intent, cycle draft, and publication.

Each exact-revision gate is approved, revised or cancelled here; approving a
draft atomically activates a cycle, and recording a publication disposition
returns the root to IDLE.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_orchestrator.api.dependencies import get_container
from agent_orchestrator.app.use_cases.publish_cycle import publish_cycle
from agent_orchestrator.infra.git.repository_binding import default_branch_of
from agent_orchestrator.app.use_cases.cyclic_planning import (
    activate_cycle,
    approve_intent,
    cancel_cycle_draft,
    cancel_intent,
    propose_intent,
    revise_cycle_draft,
    revise_intent,
    submit_cycle_draft as submit_cycle_draft_use_case,
)
from agent_orchestrator.infra.container import AppContainer


from agent_orchestrator.api.routers.plans.schemas import (
    CycleDraftRequest,
    IntentProposalRequest,
    PublicationRequest,
    ReviewDecisionRequest,
)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/{plan_id}/intent", status_code=201)
def propose_intent_route(
    plan_id: str,
    body: IntentProposalRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    proposal = propose_intent(
        plan_id,
        objective=body.objective,
        scope=body.scope,
        constraints=body.constraints,
        exclusions=body.exclusions,
        kind=body.kind,
        planner_session_ref=body.planner_session_ref,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return proposal.model_dump(mode="json")


@router.put("/{plan_id}/intent")
def revise_intent_route(
    plan_id: str,
    body: IntentProposalRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    proposal = revise_intent(
        plan_id,
        objective=body.objective,
        scope=body.scope,
        constraints=body.constraints,
        exclusions=body.exclusions,
        planner_session_ref=body.planner_session_ref,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return proposal.model_dump(mode="json")


@router.delete("/{plan_id}/intent", status_code=204)
def cancel_intent_route(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> None:
    cancel_intent(
        plan_id,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )


@router.post("/{plan_id}/intent/approve", status_code=204)
def approve_intent_route(
    plan_id: str,
    body: ReviewDecisionRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    approve_intent(
        plan_id,
        body.gate_id,
        body.subject_revision,
        container.new_unit_of_work(),
        container.clock,
    )


@router.post("/{plan_id}/cycle-draft", status_code=201)
def submit_cycle_draft_route(
    plan_id: str,
    body: CycleDraftRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    draft = submit_cycle_draft_use_case(
        plan_id,
        goals=body.goals,
        unfinished_source_treatment=body.unfinished_source_treatment,
        uow=container.new_unit_of_work(),
    )
    return draft.model_dump(mode="json")


@router.put("/{plan_id}/cycle-draft")
def revise_cycle_draft_route(
    plan_id: str,
    body: CycleDraftRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    draft = revise_cycle_draft(
        plan_id,
        goals=body.goals,
        unfinished_source_treatment=body.unfinished_source_treatment,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return draft.model_dump(mode="json")


@router.delete("/{plan_id}/cycle-draft", status_code=204)
def cancel_cycle_draft_route(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> None:
    cancel_cycle_draft(
        plan_id,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )


@router.post("/{plan_id}/cycle-draft/approve", status_code=201)
def activate_cycle_route(
    plan_id: str,
    body: ReviewDecisionRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    cycle = activate_cycle(
        plan_id,
        body.gate_id,
        body.subject_revision,
        container.new_unit_of_work(),
        container.clock,
    )
    return cycle.model_dump(mode="json")


@router.post("/{plan_id}/publication", status_code=204)
def publish_cycle_route(
    plan_id: str,
    body: PublicationRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
    # A cyclic plan is always project-bound; a legacy unbound row quarantines as
    # BLOCKED long before it can reach a publication gate, so this is the
    # narrowing mypy needs rather than a case to handle.
    project_id = plan.project_id
    assert project_id is not None
    project = container.project_repo.get(project_id)
    repo_path = container.workspace_resolver.repository_path_for(project)
    publish_cycle(
        plan_id=plan_id,
        gate_id=body.gate_id,
        revision=body.subject_revision,
        disposition=body.disposition,
        output_reference=body.output_reference,
        uow_factory=container.new_unit_of_work,
        clock=container.clock,
        forge=container.forge_for(project_id),
        repo_path=repo_path,
        default_branch=default_branch_of(repo_path) or "main",
    )


