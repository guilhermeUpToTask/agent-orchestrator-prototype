"""Unit tests for GET /api/plans/{plan_id}/attempts/{attempt_id}/log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import dependencies
from src.api.exceptions import register_exception_handlers
from src.api.routers import plans
from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
)
from src.app.testing.execution_records import InMemoryExecutionRecordRepository
from src.app.testing.fakes import InMemoryOutbox, InMemoryPlanRepository, InMemoryUnitOfWork
from src.infra.runtime.process_supervisor import attempt_log_path

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


class _FakeContainer:
    """Minimal container surface for the attempt-log endpoint."""

    def __init__(self, orchestrator_home: Path, executions: InMemoryExecutionRecordRepository) -> None:
        self.orchestrator_home = orchestrator_home
        self._executions = executions
        self._plans = InMemoryPlanRepository()
        self._outbox = InMemoryOutbox()

    def new_unit_of_work(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self._plans, self._outbox, executions=self._executions)


@pytest.fixture
def executions() -> InMemoryExecutionRecordRepository:
    return InMemoryExecutionRecordRepository()


@pytest.fixture
def container(tmp_path: Path, executions: InMemoryExecutionRecordRepository) -> _FakeContainer:
    return _FakeContainer(tmp_path, executions)


@pytest.fixture
def client(container: _FakeContainer):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(plans.router, prefix="/api")
    app.dependency_overrides[dependencies.get_container] = lambda: container
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_attempt(
    executions: InMemoryExecutionRecordRepository,
    *,
    plan_id: str,
    attempt_id: str,
    run_id: str = "run-1",
    goal_id: str = "g1",
    task_id: str = "t1",
) -> None:
    # seed outside a UoW via a short transaction (matches production write path)
    uow = InMemoryUnitOfWork(InMemoryPlanRepository(), InMemoryOutbox(), executions=executions)
    with uow:
        uow.executions.add_run(
            ExecutionRun(
                id=run_id,
                plan_id=plan_id,
                goal_id=goal_id,
                task_id=task_id,
                status=ExecutionRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        uow.executions.add_attempt(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                plan_id=plan_id,
                goal_id=goal_id,
                task_id=task_id,
                number=1,
                task_attempt=1,
                status=ExecutionAttemptStatus.RUNNING,
                started_at=NOW,
            )
        )


def _write_log(home: Path, attempt_id: str, records: list[dict]) -> Path:
    path = attempt_log_path(home, attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
        encoding="utf-8",
    )
    return path


def test_attempt_log_happy_path_returns_last_n_entries(
    client: TestClient,
    container: _FakeContainer,
    executions: InMemoryExecutionRecordRepository,
) -> None:
    plan_id, attempt_id = "plan-a", "attempt-1"
    _seed_attempt(executions, plan_id=plan_id, attempt_id=attempt_id)
    _write_log(
        container.orchestrator_home,
        attempt_id,
        [
            {"monotonic_seconds": 1.0, "stream": "stdout", "text": "one\n"},
            {"monotonic_seconds": 2.0, "stream": "stdout", "text": "two\n"},
            {"monotonic_seconds": 3.0, "stream": "stderr", "text": "three\n"},
            {"monotonic_seconds": 4.0, "stream": "stdout", "text": "four\n"},
        ],
    )

    response = client.get(f"/api/plans/{plan_id}/attempts/{attempt_id}/log?tail_lines=2")
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    assert [e["text"] for e in body["entries"]] == ["three\n", "four\n"]
    assert body["entries"][0]["stream"] == "stderr"
    assert body["entries"][1]["stream"] == "stdout"


def test_attempt_log_unknown_attempt_id_returns_404(client: TestClient) -> None:
    response = client.get("/api/plans/plan-a/attempts/missing-attempt/log")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"


def test_attempt_log_wrong_plan_ownership_returns_404_not_leak(
    client: TestClient,
    executions: InMemoryExecutionRecordRepository,
) -> None:
    """An attempt belonging to another plan must 404 like a missing attempt."""
    _seed_attempt(
        executions,
        plan_id="plan-owner",
        attempt_id="attempt-shared",
        run_id="run-owner",
    )

    response = client.get("/api/plans/plan-other/attempts/attempt-shared/log")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "ATTEMPT_NOT_FOUND"
    # no information leak about the real plan
    assert "plan-owner" not in response.text


def test_attempt_log_missing_file_returns_empty_list_not_404(
    client: TestClient,
    executions: InMemoryExecutionRecordRepository,
) -> None:
    plan_id, attempt_id = "plan-a", "attempt-no-log"
    _seed_attempt(executions, plan_id=plan_id, attempt_id=attempt_id)

    response = client.get(f"/api/plans/{plan_id}/attempts/{attempt_id}/log")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "truncated": False}


def test_attempt_log_skips_malformed_jsonl_lines(
    client: TestClient,
    container: _FakeContainer,
    executions: InMemoryExecutionRecordRepository,
) -> None:
    plan_id, attempt_id = "plan-a", "attempt-messy"
    _seed_attempt(executions, plan_id=plan_id, attempt_id=attempt_id, run_id="run-messy")
    path = attempt_log_path(container.orchestrator_home, attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"truncated":true}',
                "not-json-at-all",
                '{"monotonic_seconds":1.0,"stream":"stdout","text":"ok\\n"}',
                '{"monotonic_seconds":"bad","stream":"stdout","text":"nope"}',
                '{"incomplete":',
                '{"monotonic_seconds":2.0,"stream":"stderr","text":"also-ok\\n"}',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/plans/{plan_id}/attempts/{attempt_id}/log")
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert [e["text"] for e in body["entries"]] == ["ok\n", "also-ok\n"]
