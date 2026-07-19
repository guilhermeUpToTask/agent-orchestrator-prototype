"""The assembled execution stack: drive_plan on the REAL SQLite UoW + the REAL
git-branching workspace + the dummy runner + the SQLite agent-event sink —
success commits to the plan branch, failure discards (rollback), events land."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from src.app.testing.fakes import (
    DummyAgentRunner,
    DummyBehavior,
    FakeClock,
    InMemoryAgentRepository,
)
from src.app.use_cases.run_worker import drive_plan
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.value_objects.lifecycle import Status
from src.infra.db.agent_event_sink import SqliteAgentEventSink
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.reference_repos import SqliteProjectRepository
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.git.workspace import GitBranchWorkspace
from tests.support import make_agent_spec

pytestmark = pytest.mark.integration


class WritingDummyRunner(DummyAgentRunner):
    """Dummy that actually writes a file into the workspace so the git flow has
    something real to commit or roll back."""

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        (Path(workspace.path) / f"{task.id}.txt").write_text(f"work for {task.id}\n")
        return await super().run(
            task,
            spec,
            idempotency_key=idempotency_key,
            event_sink=event_sink,
            workspace=workspace,
        )


def _plan():
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[
            Goal(
                id="g1",
                name="g1",
                position=0,
                description="",
                tasks=[
                    Task(id="t0", name="t0", position=0, description="", agent_id="a1"),
                    Task(id="t1", name="t1", position=1, description="", agent_id="a1"),
                ],
            )
        ],
    )


def test_full_stack_drive_success_and_rollback(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'o.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    SqliteProjectRepository(sf).add(
        ProjectDefinition(id="project-1", name="Test project", repo_url=None)
    )
    clock = FakeClock()
    uow = SqliteUnitOfWork(sf, clock)
    repo_dir = tmp_path / "project-repo"
    workspace = GitBranchWorkspace(repo_dir)
    sink = SqliteAgentEventSink(sf)
    # t1 fails twice (workspace discarded twice), then succeeds
    runner = WritingDummyRunner({"t1": DummyBehavior(fail_times=2, emit_events=1)})
    agents = InMemoryAgentRepository([make_agent_spec()], "a1")

    with uow:
        uow.plans.save(_plan())

    async def drive_until_gate():
        signal = "continue"
        while signal in ("continue", "not_ready"):
            signal, _ = await drive_plan("p1", uow, runner, agents, workspace, sink, clock, "w1")
            if signal == "not_ready":
                clock.advance(120)  # wait out the backoff gate
        return signal

    assert asyncio.run(drive_until_gate()) == "paused"

    with uow:
        final = uow.plans.get("p1")
    assert final.phase == PlanPhase.REVIEW
    assert all(t.status == Status.DONE for t in final.goals[0].tasks)

    # git: only committed attempts are on the plan branch
    files = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-tree", "-r", "--name-only", "plan/p1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "t0.txt" in files and "t1.txt" in files
    # discarded attempts left no task branches behind
    stale = subprocess.run(
        ["git", "-C", str(repo_dir), "branch", "--list", "task/*"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert stale == ""

    # agent events landed in SQLite (best-effort sink), tagged per attempt
    with sf() as s:
        rows = s.execute(text("SELECT task_id, attempt, type FROM agent_events ORDER BY id")).all()
    assert ("t1", 3, "step") in [(r[0], r[1], r[2]) for r in rows]
