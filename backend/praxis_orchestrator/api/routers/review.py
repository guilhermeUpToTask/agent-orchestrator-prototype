"""GET /api/plans/{plan_id}/cycles/{cycle_id}/review — a cycle split into
review-sized units, each with its evidence and the local command that opens the
same thing.

A cycle branch is one large diff. Review research puts defect detection near 87%
under 100 changed lines and near 28% over 1,000, so handing somebody a
thousand-line diff and asking them to trust it is the worst part of the
workflow. The orchestrator is the only component that can split it, because it
recorded the boundaries: which task produced which commit, which commit was the
test that went RED first and which was the implementation that made it GREEN,
and what the protected scope was.

Read-only, and explicitly NOT hunk-level accept/reject. Half-accepting a
candidate invalidates the revision-bound evidence that makes it trustworthy, so
acceptance stays at the granularity the orchestrator can actually verify.

Every unit is paired with `local_command`. The operator's difftool and editor
are better than anything served here, so this complements them rather than
competing: the browser answers "what should I look at first", the terminal
answers "show me".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.app.branch_names import cycle_branch
from praxis_orchestrator.domain.entities.planning_artifacts import Cycle
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.errors import CycleNotFoundError, InfrastructureError
from praxis_orchestrator.infra.git.review_reader import (
    DiffStat,
    GitReviewReader,
    ReviewDiffUnavailable,
)

router = APIRouter(prefix="/plans", tags=["review"])


class FileChangeResponse(BaseModel):
    path: str
    insertions: int
    deletions: int
    binary: bool


class DiffStatResponse(BaseModel):
    files_changed: int
    insertions: int
    deletions: int
    changed_lines: int
    # small | moderate | large | very_large — from the review research, served
    # so the client need not re-derive thresholds it would get wrong.
    review_band: str
    files: list[FileChangeResponse]


class ReviewUnitResponse(BaseModel):
    """One reviewable thing: a commit, or a goal's whole merge.

    `kind` is the orchestrator's own record of what this commit WAS, which is
    the part no generic diff viewer can tell you. A `test_authoring` unit is the
    check that was proven RED before the implementation existed; reading those
    two separately is a different and much better experience than reading their
    sum.
    """

    kind: str  # goal_merge | test_authoring | implementation
    sha: str
    base: str
    resolved: bool  # False when the SHA no longer exists in the repository
    diff: DiffStatResponse | None
    local_command: str
    unavailable_reason: str | None


class TaskReviewResponse(BaseModel):
    task_id: str
    name: str
    status: str
    verification_command: str | None
    exit_code: int | None
    allowed_scope: list[str]
    forbidden_scope: list[str]
    units: list[ReviewUnitResponse]


class GoalReviewResponse(BaseModel):
    goal_id: str
    name: str
    status: str
    merge: ReviewUnitResponse | None
    tasks: list[TaskReviewResponse]


class CycleReviewResponse(BaseModel):
    plan_id: str
    cycle_id: str
    repository_path: str
    default_branch: str | None
    cycle_branch: str
    whole_cycle: ReviewUnitResponse | None
    goals: list[GoalReviewResponse]


class PatchResponse(BaseModel):
    base: str
    head: str
    patch: str
    truncated: bool
    total_bytes: int
    local_command: str


def _stat_response(stat: DiffStat) -> DiffStatResponse:
    return DiffStatResponse(
        files_changed=stat.files_changed,
        insertions=stat.insertions,
        deletions=stat.deletions,
        changed_lines=stat.changed_lines,
        review_band=stat.review_band,
        files=[
            FileChangeResponse(
                path=item.path,
                insertions=item.insertions,
                deletions=item.deletions,
                binary=item.binary,
            )
            for item in stat.files
        ],
    )


def _unit(
    reader: GitReviewReader,
    repo,
    *,
    kind: str,
    sha: str,
    base: str,
    merge: bool = False,
) -> ReviewUnitResponse:
    """Build one unit, degrading to a stated reason rather than failing the page.

    A garbage-collected commit, a repository that moved, an unreadable clone —
    each makes ONE unit unavailable and says so. Failing the whole document
    because a single old SHA vanished would hide the units that are still fine.
    """
    command = f"git -C {repo} diff {base}..{sha}"
    if not reader.resolves(repo, sha):
        return ReviewUnitResponse(
            kind=kind,
            sha=sha,
            base=base,
            resolved=False,
            diff=None,
            local_command=command,
            unavailable_reason=(
                f"commit {sha[:8]} is no longer in this repository; it may have been "
                "garbage-collected or the project was re-bound to a different clone"
            ),
        )
    try:
        stat = reader.merge_stat(repo, sha) if merge else reader.diff_stat(repo, base, sha)
    except ReviewDiffUnavailable as exc:
        return ReviewUnitResponse(
            kind=kind,
            sha=sha,
            base=base,
            resolved=True,
            diff=None,
            local_command=command,
            unavailable_reason=str(exc),
        )
    return ReviewUnitResponse(
        kind=kind,
        sha=sha,
        base=base,
        resolved=True,
        diff=_stat_response(stat),
        local_command=command,
        unavailable_reason=None,
    )


@router.get(
    "/{plan_id}/cycles/{cycle_id}/review",
    response_model=CycleReviewResponse,
)
def get_cycle_review(
    plan_id: str,
    cycle_id: str,
    container: AppContainer = Depends(get_container),
) -> CycleReviewResponse:
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        promotions = uow.promotions.list_for_cycle(plan_id, cycle_id)

    cycle: Cycle | None = next((item for item in plan.cycles if item.id == cycle_id), None)
    if cycle is None:
        raise CycleNotFoundError(plan_id, cycle_id)

    project_id = plan.project_id
    if project_id is None:
        raise InfrastructureError(
            f"plan {plan_id} is not bound to a project, so its work has no repository",
            code="PROJECT_BINDING_INVALID",
        )
    project = container.project_repo.get(project_id)
    repo = container.workspace_resolver.repository_path_for(project)
    reader = GitReviewReader()

    from praxis_orchestrator.infra.git.repository_binding import default_branch_of

    default_branch = default_branch_of(repo)
    branch = cycle_branch(cycle_id)
    by_goal = {item.goal_id: item for item in promotions}

    whole_cycle = (
        _unit(reader, repo, kind="whole_cycle", sha=branch, base=default_branch)
        if default_branch
        else None
    )

    goals: list[GoalReviewResponse] = []
    for goal in cycle.goals:
        promotion = by_goal.get(goal.id)
        merge = (
            _unit(
                reader,
                repo,
                kind="goal_merge",
                sha=promotion.merge_sha,
                base=f"{promotion.merge_sha}^1",
                merge=True,
            )
            if promotion is not None
            else None
        )

        tasks: list[TaskReviewResponse] = []
        for task in goal.tasks:
            accepted = [
                item
                for item in task.verification_evidence
                if item.accepted and item.task_revision == task.revision
            ]
            units: list[ReviewUnitResponse] = []
            seen: set[str] = set()
            for evidence in accepted:
                # The two halves the orchestrator alone can separate. Reading
                # the test that was RED first apart from the implementation
                # that made it GREEN is the whole point of this surface.
                for kind, sha in (
                    ("test_authoring", evidence.test_commit_sha),
                    ("implementation", evidence.candidate_commit_sha),
                ):
                    if not sha or sha in seen:
                        continue
                    seen.add(sha)
                    units.append(_unit(reader, repo, kind=kind, sha=sha, base=f"{sha}^"))

            contract = task.contract
            tasks.append(
                TaskReviewResponse(
                    task_id=task.id,
                    name=task.name,
                    status=task.status.value,
                    verification_command=accepted[0].exact_command if accepted else None,
                    exit_code=accepted[0].exit_code if accepted else None,
                    allowed_scope=list(contract.allowed_scope) if contract else [],
                    forbidden_scope=list(contract.forbidden_scope) if contract else [],
                    units=units,
                )
            )

        goals.append(
            GoalReviewResponse(
                goal_id=goal.id,
                name=goal.name,
                status=goal.status.value,
                merge=merge,
                tasks=tasks,
            )
        )

    return CycleReviewResponse(
        plan_id=plan_id,
        cycle_id=cycle_id,
        repository_path=str(repo),
        default_branch=default_branch,
        cycle_branch=branch,
        whole_cycle=whole_cycle,
        goals=goals,
    )


@router.get(
    "/{plan_id}/cycles/{cycle_id}/review/patch",
    response_model=PatchResponse,
)
def get_review_patch(
    plan_id: str,
    cycle_id: str,
    base: str = Query(..., description="the ref this change is measured against"),
    head: str = Query(..., description="the ref being reviewed"),
    container: AppContainer = Depends(get_container),
) -> PatchResponse:
    """The patch text for one unit, bounded.

    Separate from the index on purpose: the index has to stay cheap enough to
    open on a large cycle, and a reviewer opens one unit at a time. Truncation
    is reported rather than hidden — a silently clipped patch is how somebody
    reviews half a change believing it was all of it.
    """
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
    if not any(item.id == cycle_id for item in plan.cycles):
        raise CycleNotFoundError(plan_id, cycle_id)
    project_id = plan.project_id
    if project_id is None:
        raise InfrastructureError(
            f"plan {plan_id} is not bound to a project, so its work has no repository",
            code="PROJECT_BINDING_INVALID",
        )
    project = container.project_repo.get(project_id)
    repo = container.workspace_resolver.repository_path_for(project)

    try:
        patch = GitReviewReader().patch(repo, base, head)
    except ReviewDiffUnavailable as exc:
        raise InfrastructureError(str(exc), code="REVIEW_DIFF_UNAVAILABLE") from exc

    return PatchResponse(
        base=base,
        head=head,
        patch=patch.text,
        truncated=patch.truncated,
        total_bytes=patch.total_bytes,
        local_command=f"git -C {repo} diff {base}..{head}",
    )
