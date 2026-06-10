"""
src/api/routers/spec.py — ProjectSpec endpoints.

Covers:
  GET  /spec                 read the active project spec
  POST /spec/propose         propose a spec change (writes .proposed file)
  POST /spec/validate        validate an artifact against the active spec
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import (
    CurrentSpecDep,
    ProjectNameDep,
    ProposeSpecChangeUseCaseDep,
    ValidateAgainstSpecUseCaseDep,
)
from src.api.schemas.common import ErrorResponse
from src.api.schemas.spec import (
    ProposeSpecChangeRequest,
    ProposeSpecChangeResponse,
    SpecResponse,
    ValidateSpecRequest,
    ValidateSpecResponse,
)

router = APIRouter(prefix="/spec", tags=["spec"])


@router.get(
    "",
    response_model=SpecResponse,
    summary="Get Active Spec",
    description="Returns a flattened read-only view of the active `project_spec.yaml`.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No spec file found. Run `orchestrate init` first.",
        }
    },
)
def get_spec(spec: CurrentSpecDep) -> SpecResponse:
    return SpecResponse(
        project_name=spec.name,  # spec.meta.name is the correct attribute
        version=spec.meta.version,
        objective_description=spec.objective.description,
        objective_domain=spec.objective.domain,
        forbidden_patterns=list(spec.constraints.forbidden),
    )


@router.post(
    "/propose",
    response_model=ProposeSpecChangeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Propose Spec Change",
    description=(
        "Submits a change proposal for the `ProjectSpec`. "
        "The change is written atomically to `project_spec.proposed.yaml`. "
        "The live spec is **never** mutated directly — an operator must run "
        "`orchestrate spec apply` to promote the proposal."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No active spec to base the proposal on.",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "The proposed spec would fail validation.",
        },
    },
)
def propose_spec_change(
    payload: ProposeSpecChangeRequest,
    project_name: ProjectNameDep,
    use_case: ProposeSpecChangeUseCaseDep,
) -> ProposeSpecChangeResponse:
    from src.app.usecases.propose_spec_change import ChangeProposal

    proposal = ChangeProposal(
        new_version=payload.new_version,
        new_objective_desc=payload.new_objective_desc,
        new_objective_domain=payload.new_objective_domain,
        add_forbidden=payload.add_forbidden,
        remove_forbidden=payload.remove_forbidden,
        add_required=payload.add_required,
        remove_required=payload.remove_required,
        add_directory=payload.add_directory,
        remove_directory=payload.remove_directory,
        rationale=payload.rationale,
    )
    result = use_case.execute(project_name=project_name, proposal=proposal)

    if not result.accepted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.rejection_reason,
        )
    return ProposeSpecChangeResponse(
        accepted=True,
        proposal_path=result.proposal_path,
    )


@router.post(
    "/validate",
    response_model=ValidateSpecResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate Artifact Against Spec",
    description=(
        "Checks whether the submitted artifact text (task description, file path, "
        "or dependency declaration) satisfies the active `ProjectSpec` constraints. "
        "Returns `passed=true` when no hard violations were found."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No active spec loaded.",
        }
    },
)
def validate_against_spec(
    payload: ValidateSpecRequest,
    use_case: ValidateAgainstSpecUseCaseDep,
) -> ValidateSpecResponse:
    # ValidateAgainstSpec.execute() expects keyword arguments
    result = use_case.execute(task_description=payload.artifact)
    return ValidateSpecResponse(
        passed=result.passed,
        violations=result.violations,
        warnings=result.warnings,
    )
