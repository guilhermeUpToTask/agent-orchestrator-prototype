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

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus
from agent_orchestrator.domain.entities.project_definition import ProjectDefinition
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.value_objects.lifecycle import Status
from agent_orchestrator.infra.cli.main import cli
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base
from agent_orchestrator.infra.environment.no_environment import NoEnvironment
from agent_orchestrator.infra.worker.main import run_worker_forever

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


def test_a_worker_keeps_beating_while_a_goal_is_running(tmp_path, monkeypatch) -> None:
    """The regression this heartbeat design exists to prevent.

    The main loop blocks in `asyncio.wait(inflight, FIRST_COMPLETED)` for the
    whole of a long goal. A heartbeat written from the loop body would go silent
    for exactly that window and report a BUSY worker as dead — worse than
    reporting nothing, because an operator would restart a worker that is
    working. A test that only beats an IDLE worker passes against that broken
    design, so this one holds a goal open and watches the beat continue.
    """
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setattr("agent_orchestrator.infra.worker.main._HEARTBEAT_SECONDS", 0.05)
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
        brief="one slow goal",
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
                    )
                ],
            )
        ],
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    running = asyncio.Event()
    release = asyncio.Event()
    real_runner = container.agent_runner

    class _HeldRunner:
        """Holds the goal open so the coordinator loop is parked in
        asyncio.wait() — the exact window the old design went quiet in."""

        async def run(self, task, spec, **kwargs):
            running.set()
            await release.wait()
            return await real_runner.run(task, spec, **kwargs)

    monkeypatch.setitem(container.__dict__, "agent_runner", _HeldRunner())

    stop = asyncio.Event()
    seen: list[str] = []

    async def scenario() -> None:
        worker = asyncio.ensure_future(
            run_worker_forever(
                container,
                worker_id="beat-worker",
                poll_seconds=0.05,
                lease_seconds=30,
                stop=stop,
                max_concurrent_goals=1,
            )
        )
        try:
            await asyncio.wait_for(running.wait(), timeout=10)
            for _ in range(20):  # ~1s of held goal, >> the 0.05s beat
                rows = container.worker_registry.list_workers()
                if rows:
                    seen.append(rows[0].last_seen_at.isoformat())
                await asyncio.sleep(0.05)
        finally:
            release.set()
            stop.set()
            await worker

    asyncio.run(scenario())

    assert seen, "the worker never reported at all while a goal was in flight"
    assert len(set(seen)) > 1, (
        "last_seen_at never advanced while a goal was running — the heartbeat is "
        f"tied to the coordinator loop, which parks in asyncio.wait(): {set(seen)}"
    )


def test_the_worker_hands_its_drivers_the_environment_the_container_built(
    tmp_path, monkeypatch
) -> None:
    """The composition root must actually pass what it composed.

    `AppContainer.environment` and `.environment_context` were fully
    implemented and NOTHING in the worker ever passed them, so
    `environment.mode = container` booted nothing in production: the P8.2/P8.5
    acceptance run only ever fired in tests, because every test harness wired
    the environment by hand into its own `drive_plan`/`drive_goal` call. This
    drives the REAL entrypoint and captures what the drivers were handed.
    """
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
        brief="one goal",
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
                    )
                ],
            )
        ],
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    # A sentinel adapter, installed where the container caches the real one, so
    # the assertion is identity: whatever the container built is what the
    # drivers receive.
    sentinel = NoEnvironment()
    monkeypatch.setitem(container.__dict__, "environment", sentinel)

    seen: dict[str, dict] = {}
    stop = asyncio.Event()

    async def _fake_worker_tick(*args, **kwargs):
        seen.setdefault("worker_tick", kwargs)
        return False

    async def _fake_drive_goal(*args, **kwargs):
        seen.setdefault("drive_goal", kwargs)
        stop.set()
        return ("paused", 0)

    monkeypatch.setattr(
        "agent_orchestrator.app.use_cases.run_worker.worker_tick", _fake_worker_tick
    )
    monkeypatch.setattr(
        "agent_orchestrator.app.use_cases.run_worker.drive_goal", _fake_drive_goal
    )

    async def scenario() -> None:
        worker = asyncio.ensure_future(
            run_worker_forever(
                container,
                worker_id="wiring-worker",
                poll_seconds=0.05,
                lease_seconds=30,
                stop=stop,
                max_concurrent_goals=1,
            )
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        finally:
            stop.set()
            await worker

    asyncio.run(scenario())

    for caller in ("worker_tick", "drive_goal"):
        assert caller in seen, f"{caller} was never reached by the worker loop"
        assert seen[caller].get("environment") is sentinel, (
            f"{caller} did not receive the container's environment adapter — "
            "the acceptance run is dead code in production"
        )
        assert seen[caller].get("environment_context") is not None, (
            f"{caller} received no environment_context, so the adapter it did "
            "get can never resolve a repository to boot"
        )
