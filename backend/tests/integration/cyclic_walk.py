"""The canonical cyclic walk, callable.

Extracted from test_default_cyclic_execution.py so the evidence read model can
be asserted against a genuinely completed dry-run cycle rather than a hand-built
fixture that happens to look like one. Tier 0 only: stub reasoner, dry-run
runner.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from agent_orchestrator.api.server import create_app
from agent_orchestrator.app.handlers.planning_handler import PlanningHandler
from agent_orchestrator.app.use_cases.claim_ready_goal import claim_ready_goal
from agent_orchestrator.app.use_cases.cyclic_planning import (
    activate_cycle,
    approve_intent,
    propose_intent,
    record_output_disposition,
)
from agent_orchestrator.app.use_cases.run_worker import drive_goal, drive_plan
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan
from agent_orchestrator.domain.entities.planning_artifacts import (
    OutputDisposition,
    PlanStatus,
    ProposalKind,
    ReviewSubjectType,
)
from agent_orchestrator.domain.entities.project_definition import ProjectDefinition
from agent_orchestrator.infra.cli.main import cli
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base


@dataclass
class CompletedCycle:
    client: TestClient
    plan_id: str
    cycle_id: str
    repo: Path
    container: object  # AppContainer; typed loosely to avoid an infra import here


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_trunk_repo(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "trunk", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )


def drive_cycle_to_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    disposition: str = "merge",
    output_reference: str | None = "cycle/merged",
    publish: bool = True,
    environment: object | None = None,
    on_ready: object | None = None,
) -> CompletedCycle:
    """Drive intent -> draft -> cycle -> enrichment -> execution -> publication."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    # Left unset: the control plane stays open in local/test dev
    # (`agent_orchestrator/api/security.py`), so the returned client needs no token.
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    seeded = CliRunner().invoke(cli, ["seed", "demo", "--stub"])
    assert seeded.exit_code == 0, seeded.output

    repo = tmp_path / "project"
    _init_trunk_repo(repo)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Project", repo_url=str(repo))
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="deliver a verified dry-run cycle",
        status=PlanStatus.IDLE,
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    proposal = propose_intent(
        plan.id,
        objective="deliver a verified dry-run cycle",
        scope=["."],
        constraints=["deterministic"],
        exclusions=[],
        kind=ProposalKind.INITIAL,
        planner_session_ref=None,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    with container.new_unit_of_work() as uow:
        waiting = uow.plans.get(plan.id)
    assert waiting.review_gate is not None
    approve_intent(
        plan.id,
        waiting.review_gate.id,
        proposal.revision,
        container.new_unit_of_work(),
        container.clock,
    )

    planning = PlanningHandler(
        container.reasoner,
        container.agent_repo,
        container.capability_repo,
        container.clock,
    )

    async def drive() -> tuple[str, int]:
        return await drive_plan(
            plan.id,
            container.new_unit_of_work(),
            replace(container.execution_services, environment=environment),
            "worker-1",
            planning_handler=planning,
        )

    architecture_signal, _ = asyncio.run(drive())
    assert architecture_signal == "paused"
    with container.new_unit_of_work() as uow:
        drafted = uow.plans.get(plan.id)
    assert drafted.cycle_draft is not None
    assert drafted.review_gate is not None

    cycle = activate_cycle(
        plan.id,
        drafted.review_gate.id,
        drafted.cycle_draft.revision,
        container.new_unit_of_work(),
        container.clock,
    )

    # Domain unfreeze #14 (symmetric per-goal leases): the plan-level tick
    # only drives enrichment/gates for a cyclic plan now, never execution --
    # drive() settles JIT enrichment and then has nothing left to do at the
    # plan level (every ready goal is enriched, none are all-terminal yet).
    enrichment_signal, enrichment_progressed = asyncio.run(drive())
    assert enrichment_signal == "not_ready"
    assert enrichment_progressed >= 1

    with container.new_unit_of_work() as uow:
        enriched = uow.plans.get(plan.id)
    assert enriched.active_cycle is not None
    goal_id = enriched.active_cycle.goals[0].id

    if on_ready is not None:
        on_ready(container, plan.id)  # type: ignore[operator]

    claimed = claim_ready_goal(container.new_unit_of_work(), "worker-1", 60, container.clock)
    assert claimed == (plan.id, goal_id)

    async def drive_goal_() -> tuple[str, int]:
        return await drive_goal(
            plan.id,
            goal_id,
            container.new_unit_of_work(),
            replace(container.execution_services, environment=environment),
            "worker-1",
        )

    goal_signal, goal_progressed = asyncio.run(drive_goal_())
    # The goal drives to DONE-and-promoted (CONTINUE), then peek_next_for_goal
    # recognizes it's already terminal and stops (PAUSED) rather than
    # incorrectly opening the plan-wide completion gate itself -- that's the
    # plan-level tick's job, exercised next.
    assert goal_signal == "paused"
    assert goal_progressed >= 1

    review_signal, _ = asyncio.run(drive())
    assert review_signal == "paused"
    with container.new_unit_of_work() as uow:
        completed = uow.plans.get(plan.id)
    assert completed.status == PlanStatus.WAITING
    assert completed.review_gate is not None
    assert completed.review_gate.subject_type == ReviewSubjectType.CYCLE_COMPLETION
    assert completed.active_cycle is not None
    assert completed.active_cycle.id == cycle.id

    if publish:
        record_output_disposition(
            plan.id,
            completed.review_gate.id,
            completed.review_gate.subject_revision,
            OutputDisposition(disposition),
            output_reference,
            container.new_unit_of_work(),
            container.clock,
        )

    client = TestClient(create_app(container))
    return CompletedCycle(
        client=client,
        plan_id=plan.id,
        cycle_id=cycle.id,
        repo=repo,
        container=container,
    )
