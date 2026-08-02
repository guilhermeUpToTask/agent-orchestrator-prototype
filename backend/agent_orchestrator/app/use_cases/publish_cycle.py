"""Publication: the one place a side effect must precede a recorded decision.

`record_output_disposition` does everything inside one transaction, which is
correct when the disposition is a claim a human typed. When the orchestrator
opens the pull request itself, the push and the API call are side effects, and
architectural invariant #5 says those never run inside a transaction.

So the order here is deliberate and load-bearing:

  1. read-only pre-check, no transaction  -- cheap refusal before anything external
  2. push, then open the PR, outside      -- the side effects
  3. record the disposition               -- only now, with the real URL

A failure at step 2 leaves the gate open and nothing written. The operator can
retry, or choose `retain_branch` instead. `output_reference` stops being the one
place a human asserts something the system cannot verify.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import structlog

from agent_orchestrator.app.branch_names import cycle_branch
from agent_orchestrator.app.forge_port import ForgeNotConfiguredError, ForgePort
from agent_orchestrator.app.pr_body import EvidenceLine, render_pr_body, render_pr_title
from agent_orchestrator.app.ports import Clock, UnitOfWork
from agent_orchestrator.app.use_cases.cyclic_planning import record_output_disposition
from agent_orchestrator.domain.entities.planning_artifacts import Cycle, OutputDisposition

log = structlog.get_logger(__name__)


def _accepted_evidence(cycle: Cycle) -> list[EvidenceLine]:
    """One line per accepted piece of evidence, in goal then task order.

    Reads only what the aggregate already holds; a task with no accepted
    evidence simply contributes nothing, because a cycle reaching publication
    without any is a real state (an empty or fully discarded cycle) and not a
    reason to refuse to publish.
    """
    lines: list[EvidenceLine] = []
    for goal in cycle.goals:
        for task in goal.tasks:
            for evidence in task.verification_evidence:
                if not evidence.accepted:
                    continue
                lines.append(
                    EvidenceLine(
                        task_title=task.name,
                        command=evidence.exact_command,
                        exit_code=evidence.exit_code,
                        candidate_commit_sha=evidence.candidate_commit_sha,
                        test_commit_sha=evidence.test_commit_sha,
                    )
                )
    return lines


def publish_cycle(
    *,
    plan_id: str,
    gate_id: str,
    revision: int,
    disposition: OutputDisposition,
    output_reference: str | None,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    forge: ForgePort,
    repo_path: Path,
    default_branch: str,
) -> str | None:
    """Record one output disposition, opening a real pull request when asked to
    and able. Returns the reference that was actually recorded."""
    if disposition != OutputDisposition.OPEN_PR:
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference, uow_factory(), clock
        )
        return output_reference

    # 1. Read-only pre-check, in a transaction that CLOSES before any side
    #    effect runs. The SQLite repository refuses a read outside a UnitOfWork,
    #    so this is a `with` block rather than a bare call — but the point of
    #    invariant #5 is preserved exactly: nothing external happens while a
    #    transaction is open, and this one writes nothing.
    with uow_factory() as uow:
        plan = uow.plans.get(plan_id)
    cycle = plan.active_cycle
    if cycle is None:
        # Nothing to publish. Let the aggregate produce the canonical refusal
        # rather than inventing a second one here.
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference, uow_factory(), clock
        )
        return output_reference

    branch = cycle_branch(cycle.id)
    objective = cycle.approved_intent.objective if cycle.approved_intent else ""

    # 2. Side effects, OUTSIDE any transaction.
    try:
        forge.push_branch(repo_path, branch)
        pull_request = forge.open_pull_request(
            head=branch,
            base=default_branch,
            title=render_pr_title(objective, cycle.id),
            body=render_pr_body(
                cycle_id=cycle.id,
                objective=objective,
                evidence=_accepted_evidence(cycle),
                goal_count=len(cycle.goals),
            ),
        )
    except ForgeNotConfiguredError:
        # No forge bound: exactly the behaviour that existed before this port,
        # a reference the operator typed. Not an error — a supported setup.
        log.info("publication.no_forge", plan_id=plan_id, cycle_id=cycle.id)
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference, uow_factory(), clock
        )
        return output_reference

    # 3. Record it, re-reading and re-guarding inside the transaction.
    log.info(
        "publication.pull_request_opened",
        plan_id=plan_id,
        cycle_id=cycle.id,
        number=pull_request.number,
    )
    record_output_disposition(
        plan_id, gate_id, revision, disposition, pull_request.url, uow_factory(), clock
    )
    return pull_request.url
