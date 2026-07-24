"""Domain unfreeze #14 (symmetric per-goal leases + dynamic goal-worker
pool): the actual operational promise this unfreeze makes — a SINGLE
`orchestrate worker start` process drives multiple independent, ready goals
CONCURRENTLY, without an operator hand-starting a second OS process the way
last session's live walkthrough required. This drives a real (real-SQLite,
real git worktrees, dry-run agent runner) two-goal cyclic plan through ONE
`run_worker_forever` call and confirms both goals reach DONE."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status
from src.infra.cli.main import cli
from src.infra.container import AppContainer
from src.infra.db.tables import Base
from src.infra.worker.main import run_worker_forever

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_trunk_repo(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "trunk", str(repo)], check=True, capture_output=True, text=True
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


def test_single_worker_process_drives_two_independent_goals_concurrently(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    seeded = CliRunner().invoke(cli, ["seed", "demo", "--stub"])
    assert seeded.exit_code == 0, seeded.output

    repo = tmp_path / "project"
    _init_trunk_repo(repo)
    container.project_repo.add(ProjectDefinition(id="project-1", name="Project", repo_url=str(repo)))

    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="two independent goals",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                status=CycleStatus.ACTIVE,
                started_at=NOW,
                goals=[
                    Goal(
                        id="g1",
                        name="g1",
                        position=0,
                        description="",
                        tasks=[
                            Task(
                                id="g1-t",
                                name="g1-t",
                                position=0,
                                description="",
                                agent_id="dev-agent",
                            )
                        ],
                    ),
                    Goal(
                        id="g2",
                        name="g2",
                        position=1,
                        description="",
                        tasks=[
                            Task(
                                id="g2-t",
                                name="g2-t",
                                position=0,
                                description="",
                                agent_id="dev-agent",
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    stop = asyncio.Event()

    async def scenario() -> None:
        worker = asyncio.ensure_future(
            run_worker_forever(
                container,
                worker_id="pool-worker",
                poll_seconds=0.05,
                lease_seconds=30,
                stop=stop,
                max_concurrent_goals=4,
            )
        )
        try:
            for _ in range(200):  # bounded poll, ~10s worst case
                with container.new_unit_of_work() as uow:
                    current = uow.plans.get("plan-1")
                statuses = {g.id: g.tasks[0].status for g in current.active_cycle.goals}
                if all(status == Status.DONE for status in statuses.values()):
                    break
                await asyncio.sleep(0.05)
        finally:
            stop.set()
            await worker

    asyncio.run(scenario())

    with container.new_unit_of_work() as uow:
        final = uow.plans.get("plan-1")
    statuses = {g.id: g.tasks[0].status for g in final.active_cycle.goals}
    assert statuses == {"g1": Status.DONE, "g2": Status.DONE}, statuses
