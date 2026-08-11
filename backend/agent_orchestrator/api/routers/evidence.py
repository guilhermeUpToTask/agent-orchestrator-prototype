"""GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence — what was verified, and
where the code went.

Every fact here already existed. Accepted evidence hung four levels deep at
`active_cycle.goals[].tasks[].verification_evidence[]`, protected scope was
split between the contract and the test bundle with nothing joining it, and the
disposition sat on the cycle — all reachable only by downloading the whole plan
document, which also carries the brief, the chat and every superseded cycle.

Addressed per CYCLE rather than per plan so a superseded cycle's evidence
survives a replan and stays addressable.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_orchestrator.api.dependencies import get_container
from agent_orchestrator.app.branch_names import cycle_branch
from agent_orchestrator.domain.entities.planning_artifacts import Cycle
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.errors import (
    CycleNotFoundError,
    ProjectBindingInvalidError,
)
from agent_orchestrator.infra.git.repository_binding import (
    default_branch_of,
    validate_repo_url,
)

router = APIRouter(prefix="/plans", tags=["evidence"])

# How many `verification_baseline` artifacts to read per request. One is written
# per task-attempt that reached a frozen bundle, so this covers a large cycle
# plus its retries; past it the oldest baselines fall off and their tasks report
# `baseline: null` rather than the endpoint growing without bound.
_BASELINE_SCAN_LIMIT = 200


class PromotionResponse(BaseModel):
    from_ref: str
    into_ref: str
    merge_sha: str
    promoted_at: datetime


class ProtectedScopeResponse(BaseModel):
    """The two halves an operator previously joined by hand: what the task was
    allowed to touch, and what it was forbidden to weaken."""

    allowed_scope: list[str]
    forbidden_scope: list[str]
    protected_file_hashes: dict[str, str]
    criterion_to_tests: dict[str, list[str]]


class BaselineResponse(BaseModel):
    """What the checks did BEFORE the implementation existed.

    The single fact that decides whether the green afterwards means anything: a
    check that already passed proves nothing about this task, because the green
    after the work would be the same green as before it. The orchestrator
    already ran this and already refused to proceed without it — `tdd` and
    `executable_check` both require a FAILING baseline
    (`app/verification.py::baseline_outcome`) — but the verdict lived only in a
    `verification_baseline` planning artifact, off to one side of the evidence
    document anyone actually reads.

    So the run was red, provably, and the published evidence could not say so.
    Surfaced here (Phase 10B) because "the tests were proven failing first" is
    the claim this product rests on, and a claim a reader cannot check from the
    evidence bundle is one we should not be making.
    """

    verdict: str  # "red" | "green"
    commands: list[str]
    exit_codes: list[int]
    checks: list[str]


class TestBundleResponse(BaseModel):
    test_commit_sha: str
    state: str
    verification_strategy: str
    # Empty only for a bundle frozen before this was recorded, or if the
    # best-effort artifact write was lost — never a signal that no baseline ran,
    # because a task cannot reach `frozen` without an accepted one.
    baseline: BaselineResponse | None
    # sha256 of the baseline run's output. Proves the command ran and what it
    # printed; `baseline` above is what it decided.
    baseline_evidence_refs: list[str]


class EvidenceResponse(BaseModel):
    id: str
    run_id: str
    task_revision: int
    verification_kind: str
    exact_command: str
    exit_code: int
    candidate_commit_sha: str
    test_commit_sha: str
    bounded_output_ref: str
    finished_at: datetime


class TaskEvidenceResponse(BaseModel):
    task_id: str
    revision: int
    status: str
    protected_scope: ProtectedScopeResponse | None
    test_bundle: TestBundleResponse | None
    accepted_evidence: list[EvidenceResponse]
    rejected_evidence_count: int
    superseded_evidence_count: int


class GoalEvidenceResponse(BaseModel):
    goal_id: str
    status: str
    promotion: PromotionResponse | None
    tasks: list[TaskEvidenceResponse]


class DispositionResponse(BaseModel):
    disposition: str
    output_reference: str | None


class DeliveryResponse(BaseModel):
    """Where this cycle's work physically is, so the hand-off can be true.

    `ProjectDefinition.repo_url` decides three different topologies and they
    need three different answers. A LOCAL binding put `cycle/<id>` in the
    operator's own checkout — it is already delivered and only needs finding. A
    REMOTE binding put it in a clone the orchestrator owns, under
    `$ORCHESTRATOR_HOME/projects/<id>/repos/<sha256[:16]>`, which is nowhere the
    operator has ever looked. A SCRATCH binding produced a demo repository whose
    contents nobody wants.

    Serving these as facts rather than as a rendered instruction follows the
    same rule as `status`/`legal_actions`: the API states what is true, and the
    frontend and the API-only fixtures each render the commands they need.
    """

    binding: str
    repository_path: str
    default_branch: str | None
    cycle_branch: str
    in_operator_checkout: bool


class AcceptanceRunResponse(BaseModel):
    """One advisory acceptance-run verdict (P8.2).

    ADVISORY, and the field name says so rather than leaving a client to infer
    it. Verification proves a command exited as expected against a commit; this
    is the only thing in the document that speaks to whether the APPLICATION
    runs. It never gated the publication it appears beside.
    """

    trigger: str  # goal_merge | pre_publication
    goal_id: str | None  # None for pre_publication: it observes the whole cycle
    ref: str
    outcome: str  # passed | failed | errored
    summary: str
    detail: str
    duration_seconds: float
    created_at: str


class CycleEvidenceResponse(BaseModel):
    plan_id: str
    cycle_id: str
    cycle_status: str
    goals: list[GoalEvidenceResponse]
    disposition: DispositionResponse | None
    delivery: DeliveryResponse | None
    unattributed_evidence_refs: list[str]
    # Empty when no project environment is configured — the default, supported
    # state. An empty list means "nobody asked", never "it passed": `skipped`
    # verdicts are deliberately not recorded, so absence cannot read as a pass.
    acceptance_runs: list[AcceptanceRunResponse]


def _baselines_by_task(
    container: AppContainer, plan_id: str
) -> dict[str, BaselineResponse]:
    """The `verification_baseline` artifacts, keyed by `task_id:revision`.

    Read once per request rather than per task — a cycle has as many tasks as it
    has work, and this is a second store outside the plan document.

    Best-effort on purpose: the baseline is written on its own short transaction
    (`ExecutionHandler._record_baseline`) precisely so losing it can never fail a
    run that succeeded, so a missing artifact must degrade this endpoint to
    `baseline: null` rather than take it down.
    """
    artifacts = container.planning_artifacts
    if artifacts is None:
        return {}
    try:
        # `latest(goal_id=None)` would ask for the PLAN-WIDE artifacts and return
        # nothing: the baseline is written per goal.
        rows = artifacts.latest_across_goals(
            plan_id, "verification_baseline", limit=_BASELINE_SCAN_LIMIT
        )
    except Exception:  # pragma: no cover - a read-model extra must not 500
        return {}

    found: dict[str, BaselineResponse] = {}
    for row in rows:
        payload = row.payload or {}
        key = row.input_fingerprint or f"{payload.get('task_id')}:{payload.get('task_revision')}"
        # `latest` is newest-first, so the first hit for a key is the current one
        # and a retried task's older baselines are correctly ignored.
        if key in found:
            continue
        verdict = payload.get("verdict")
        if not isinstance(verdict, str):
            continue

        def _strings(name: str) -> list[str]:
            value = payload.get(name)
            return [str(item) for item in value] if isinstance(value, list) else []

        codes = payload.get("exit_codes")
        found[key] = BaselineResponse(
            verdict=verdict,
            commands=_strings("commands"),
            exit_codes=(
                [int(item) for item in codes if isinstance(item, int)]
                if isinstance(codes, list)
                else []
            ),
            checks=_strings("checks"),
        )
    return found


def _task_evidence(
    task: Task, baselines: dict[str, BaselineResponse] | None = None
) -> TaskEvidenceResponse:
    # Evidence bound to a superseded revision is NOT accepted evidence for the
    # current contract: `edit_task` invalidates revision-bound evidence, so
    # serving it as accepted is precisely the lie this endpoint exists to avoid.
    accepted = [
        item
        for item in task.verification_evidence
        if item.accepted and item.task_revision == task.revision
    ]
    superseded = [
        item
        for item in task.verification_evidence
        if item.accepted and item.task_revision != task.revision
    ]
    rejected = [item for item in task.verification_evidence if not item.accepted]

    contract = task.contract
    bundle = task.test_bundle
    return TaskEvidenceResponse(
        task_id=task.id,
        revision=task.revision,
        status=task.status.value,
        protected_scope=(
            None
            if contract is None
            else ProtectedScopeResponse(
                allowed_scope=list(contract.allowed_scope),
                forbidden_scope=list(contract.forbidden_scope),
                protected_file_hashes=(
                    {} if bundle is None else dict(bundle.protected_file_hashes)
                ),
                criterion_to_tests=(
                    {} if bundle is None else dict(bundle.criterion_to_tests)
                ),
            )
        ),
        test_bundle=(
            None
            if bundle is None
            else TestBundleResponse(
                test_commit_sha=bundle.test_commit_sha,
                state=bundle.state.value,
                verification_strategy=bundle.verification_strategy.value,
                baseline=(baselines or {}).get(f"{task.id}:{task.revision}"),
                baseline_evidence_refs=list(bundle.red_or_baseline_evidence_refs),
            )
        ),
        accepted_evidence=[
            EvidenceResponse(
                id=item.id,
                run_id=item.run_id,
                task_revision=item.task_revision,
                verification_kind=item.verification_kind.value,
                exact_command=item.exact_command,
                exit_code=item.exit_code,
                candidate_commit_sha=item.candidate_commit_sha,
                test_commit_sha=item.test_commit_sha,
                bounded_output_ref=item.bounded_output_ref,
                finished_at=item.finished_at,
            )
            for item in accepted
        ],
        # Counted, not inlined: the full attempt history already has a home at
        # GET .../attempts, and dumping it here would rebuild the very problem
        # this endpoint solves.
        rejected_evidence_count=len(rejected),
        superseded_evidence_count=len(superseded),
    )


def _delivery(
    container: AppContainer, project_id: str | None, cycle_id: str
) -> DeliveryResponse | None:
    """None only when the plan is not bound to a project — a legacy unbound row
    quarantined as BLOCKED has no repository to hand anything over from."""
    if project_id is None:
        return None
    project = container.project_repo.get(project_id)
    resolved_path = container.workspace_resolver.repository_path_for(project)

    try:
        kind = validate_repo_url(project.repo_url).kind
    except ProjectBindingInvalidError:
        # The binding was valid when the plan ran or the cycle could not have
        # produced evidence; the path has since moved or been deleted. Reporting
        # the topology we resolved beats refusing to answer, and the operator
        # can see for themselves that the path is gone.
        kind = "local"

    # `validate_repo_url` returns no default branch for a remote binding (it
    # never touches the network or the disk there). The clone exists by now, so
    # probe it. `local` gets the same treatment for one code path, not two.
    return DeliveryResponse(
        binding=kind,
        repository_path=str(resolved_path),
        default_branch=(
            default_branch_of(resolved_path) if resolved_path.exists() else None
        ),
        cycle_branch=cycle_branch(cycle_id),
        # The one fact that changes the instruction: is the branch already in a
        # repository the operator works in, or in one they have never seen?
        in_operator_checkout=kind == "local",
    )


@router.get(
    "/{plan_id}/cycles/{cycle_id}/evidence",
    response_model=CycleEvidenceResponse,
)
def get_cycle_evidence(
    plan_id: str,
    cycle_id: str,
    container: AppContainer = Depends(get_container),
) -> CycleEvidenceResponse:
    uow = container.new_unit_of_work()
    with uow:
        plan = uow.plans.get(plan_id)
        promotions = uow.promotions.list_for_cycle(plan_id, cycle_id)
        acceptance = uow.acceptance_runs.list_for_cycle(plan_id, cycle_id)

    # Scoped to THIS plan's cycles, so a cycle id belonging to another plan is
    # refused rather than served empty.
    cycle: Cycle | None = next(
        (item for item in plan.cycles if item.id == cycle_id), None
    )
    if cycle is None:
        raise CycleNotFoundError(plan_id, cycle_id)

    # After the cycle is resolved, so an unknown id still 404s without touching
    # a second store.
    baselines = _baselines_by_task(container, plan_id)

    by_goal = {item.goal_id: item for item in promotions}
    return CycleEvidenceResponse(
        plan_id=plan_id,
        cycle_id=cycle_id,
        cycle_status=cycle.status.value,
        acceptance_runs=[
            AcceptanceRunResponse(
                trigger=run.trigger,
                goal_id=run.goal_id,
                ref=run.ref,
                outcome=run.outcome,
                summary=run.summary,
                detail=run.detail,
                duration_seconds=run.duration_seconds,
                created_at=run.created_at.isoformat(),
            )
            for run in acceptance
        ],
        goals=[
            GoalEvidenceResponse(
                goal_id=goal.id,
                status=goal.status.value,
                promotion=(
                    None
                    if goal.id not in by_goal
                    else PromotionResponse(
                        from_ref=by_goal[goal.id].from_ref,
                        into_ref=by_goal[goal.id].into_ref,
                        merge_sha=by_goal[goal.id].merge_sha,
                        promoted_at=by_goal[goal.id].promoted_at,
                    )
                ),
                tasks=[_task_evidence(task, baselines) for task in goal.tasks],
            )
            for goal in cycle.goals
        ],
        disposition=(
            None
            if cycle.output_disposition is None
            else DispositionResponse(
                disposition=cycle.output_disposition.value,
                output_reference=cycle.output_reference,
            )
        ),
        delivery=_delivery(container, plan.project_id, cycle_id),
        # Cycles promoted before migration 0017 have SHAs with no attribution.
        # Serving them under an honest name beats an empty `promotion` that
        # would imply nothing was ever promoted.
        #
        # Matched by SHA rather than "are there any rows at all": exactly one
        # cycle per install can straddle the migration, with goals promoted
        # before it (ref, no row) and after it (both). A presence check would
        # return [] for that cycle and silently hide the pre-migration refs.
        unattributed_evidence_refs=[
            ref
            for ref in cycle.evidence_refs
            if ref not in {f"git:{item.merge_sha}" for item in promotions}
        ],
    )
