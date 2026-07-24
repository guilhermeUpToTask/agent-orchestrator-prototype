"""The thin API over TestClient: the plan lifecycle through HTTP, the error->
HTTP mapping table, reference-data CRUD with the no-plaintext secrets rule,
and two-tier config."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.domain.entities.project_definition import ProjectDefinition
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Test project", repo_url=None)
    )
    app = create_app(container)
    with TestClient(app) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_quarantined_legacy_plan_can_bind_a_project_over_http(client):
    from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
    from src.domain.entities.planning_artifacts import PlanBlock, PlanStatus

    container = dependencies.get_container()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    plan = Plan(
        id="legacy-plan",
        brief="migrated",
        project_id=None,
        phase=PlanPhase.RUNNING,
        status=PlanStatus.BLOCKED,
        legacy_mapped_status=PlanStatus.RUNNING,
        block=PlanBlock(
            id="project-binding:legacy-plan",
            kind="project_binding",
            explanation="Select the project that owns this migrated plan.",
            stage="migration",
            legal_resolutions=["bind_project"],
            created_at=now,
        ),
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    response = client.post(
        "/api/plans/legacy-plan/project-binding",
        json={"project_id": "project-1"},
    )

    assert response.status_code == 204
    with container.new_unit_of_work() as uow:
        rebound = uow.plans.get("legacy-plan")
    assert rebound.project_id == "project-1"
    assert rebound.status == PlanStatus.RUNNING
    assert rebound.block is not None
    assert rebound.block.resolution == "bind_project"
    assert rebound.block.resolved_at is not None


def test_plan_lifecycle_over_http(client):
    # create (idempotent on the Idempotency-Key header)
    created = client.post(
        "/api/plans",
        json={"brief": "goal: G1\ntask: t one", "project_id": "project-1"},
        headers={"Idempotency-Key": "req-1"},
    )
    assert created.status_code == 201
    created_body = created.json()
    plan_id = created_body["plan_id"]
    assert created_body["brief_preserved"] is True
    assert created_body["discovery_status"] == "waiting_for_user"
    again = client.post(
        "/api/plans",
        json={"brief": "goal: G1\ntask: t one", "project_id": "project-1"},
        headers={"Idempotency-Key": "req-1"},
    )
    assert again.json()["plan_id"] == plan_id

    # inspect
    fetched = client.get(f"/api/plans/{plan_id}")
    assert fetched.status_code == 200
    assert fetched.json()["phase"] == "discovery"
    assert client.get("/api/plans").status_code == 200

    # multi-turn discovery: an ask-turn replies without committing
    ask = client.post(
        f"/api/plans/{plan_id}/discovery/message",
        json={"message": "ask: which database should we use?"},
    )
    assert ask.status_code == 200
    ask_body = ask.json()
    assert ask_body["committed"] is False
    assert ask_body["operation_status"] == "waiting_for_user"
    assert "which database should we use?" in ask_body["reply"]
    assert client.get(f"/api/plans/{plan_id}").json()["phase"] == "discovery"

    # the commit turn proposes intent and opens its version-bound review gate
    turn = client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": ""})
    assert turn.status_code == 200
    body = turn.json()
    assert body["committed"] is True
    assert body["operation_status"] == "committed"
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["phase"] == "discovery"
    assert detail["pending_gate"]["subject_type"] == "intent"

    # chat history: user/assistant alternation, insertion order, commit meta
    history = client.get(f"/api/plans/{plan_id}/chat").json()
    assert [(m["role"], m["meta"].get("committed")) for m in history] == [
        ("user", None),  # automatically submitted brief
        ("assistant", False),
        ("user", None),
        ("assistant", False),
        ("user", None),
        ("assistant", True),
    ]
    assert history[0]["meta"]["submitted_brief"] is True
    assert client.get("/api/plans/ghost/chat").status_code == 404


def test_cyclic_plan_review_edits_activation_and_publication(client):
    created = client.post(
        "/api/plans",
        json={"brief": "replace the legacy lifecycle", "project_id": "project-1"},
    )
    assert created.status_code == 201
    plan_id = created.json()["plan_id"]
    initial = client.get(f"/api/plans/{plan_id}").json()
    assert initial["status"] == "waiting"
    assert initial["activity"] == "intent_discovery"
    assert initial["legal_actions"] == ["start_intent"]

    proposed = client.post(
        f"/api/plans/{plan_id}/intent",
        json={
            "objective": "first objective",
            "scope": ["backend"],
            "constraints": ["sequential"],
            "exclusions": ["parallelism"],
        },
    )
    assert proposed.status_code == 201
    old_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]

    revised = client.put(
        f"/api/plans/{plan_id}/intent",
        json={
            "objective": "approved objective",
            "scope": ["backend", "frontend"],
            "constraints": ["deterministic"],
            "exclusions": ["workflow engine"],
        },
    )
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert (
        client.post(
            f"/api/plans/{plan_id}/intent/approve",
            json={"gate_id": old_gate["id"], "subject_revision": 1},
        ).status_code
        == 422
    )
    intent_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    assert (
        client.post(
            f"/api/plans/{plan_id}/intent/approve",
            json={"gate_id": intent_gate["id"], "subject_revision": 2},
        ).status_code
        == 204
    )
    assert client.get(f"/api/plans/{plan_id}").json()["activity"] == "cycle_architecture"

    draft = client.post(
        f"/api/plans/{plan_id}/cycle-draft",
        json={
            "goals": [
                {
                    "key": "foundation",
                    "name": "Foundation",
                    "objective": "lay foundation",
                    "position": 0,
                    "depends_on": [],
                }
            ]
        },
    )
    assert draft.status_code == 201
    old_draft_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    revised_draft = client.put(
        f"/api/plans/{plan_id}/cycle-draft",
        json={
            "goals": [
                {
                    "key": "foundation",
                    "name": "Foundation",
                    "objective": "lay foundation",
                    "position": 0,
                    "depends_on": [],
                },
                {
                    "key": "delivery",
                    "name": "Delivery",
                    "objective": "ship",
                    "position": 1,
                    "depends_on": ["foundation"],
                },
            ],
            "unfinished_source_treatment": "supersede unfinished work",
        },
    )
    assert revised_draft.status_code == 200
    assert revised_draft.json()["revision"] == 2
    assert (
        client.post(
            f"/api/plans/{plan_id}/cycle-draft/approve",
            json={"gate_id": old_draft_gate["id"], "subject_revision": 1},
        ).status_code
        == 422
    )
    draft_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    activated = client.post(
        f"/api/plans/{plan_id}/cycle-draft/approve",
        json={"gate_id": draft_gate["id"], "subject_revision": 2},
    )
    assert activated.status_code == 201
    cycle_id = activated.json()["id"]
    active = client.get(f"/api/plans/{plan_id}").json()
    assert active["status"] == "running"
    assert active["active_cycle"]["id"] == cycle_id
    assert active["phase"] not in {"done", "failed"}

    # Deterministic cycle verification is the application-owned predecessor of
    # publication. Seed its accepted gate directly to isolate this HTTP contract.
    from src.api import dependencies
    from src.domain.entities.planning_artifacts import ReviewGate, ReviewSubjectType
    from src.domain.factories.identity import new_id

    container = dependencies.get_container()
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.CYCLE_COMPLETION,
            subject_id=cycle_id,
            subject_revision=1,
            allowed_decisions=["retain_branch"],
            continuation="publish",
        )
        plan.open_completion_gate(gate, ["evidence://cycle"])
        plan.bump_version()
        uow.plans.save(plan)

    gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    published = client.post(
        f"/api/plans/{plan_id}/publication",
        json={
            "gate_id": gate["id"],
            "subject_revision": 1,
            "disposition": "retain_branch",
            "output_reference": f"refs/heads/cycle/{cycle_id}",
        },
    )
    assert published.status_code == 204
    final = client.get(f"/api/plans/{plan_id}").json()
    assert final["status"] == "idle"
    assert final["active_cycle"] is None
    assert final["cycles"][0]["status"] == "completed"
    assert final["phase"] not in {"done", "failed"}


def _plan_with_enriched_active_goal(client, project_id="project-1") -> str:
    """Drive intent -> roadmap approval -> one head-goal JIT contract."""
    from src.api import dependencies
    from src.app.use_cases.advance_plan import advance_plan
    from src.app.handlers.planning_handler import PlanningHandler

    for capability_id in ("implementation", "test_authoring"):
        client.post(
            "/api/capabilities",
            json={
                "id": capability_id,
                "name": capability_id,
                "description": "",
                "tools": [],
            },
        )
    # Cyclic contracts bind both roles through mandatory capabilities.
    agent_id = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "capability_ids": ["implementation"],
        },
    ).json()["id"]
    client.post(f"/api/agents/{agent_id}/default")
    client.post(
        "/api/agents",
        json={
            "name": "Test author",
            "role": "test_author",
            "model_role": "smart",
            "capability_ids": ["test_authoring"],
        },
    )

    plan_id = client.post("/api/plans", json={"brief": "b", "project_id": project_id}).json()[
        "plan_id"
    ]
    # The create call already persisted the first waiting discovery turn.
    client.post(
        f"/api/plans/{plan_id}/discovery/message",
        json={"message": "Deliver a verified API"},
    )
    intent_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    assert (
        client.post(
            f"/api/plans/{plan_id}/intent/approve",
            json={
                "gate_id": intent_gate["id"],
                "subject_revision": intent_gate["subject_revision"],
            },
        ).status_code
        == 204
    )
    container = dependencies.get_container()
    import asyncio

    handler = PlanningHandler(
        container.reasoner,
        container.agent_repo,
        container.capability_repo,
        container.clock,
    )
    # Worker generates the roadmap and pauses at its review gate.
    asyncio.run(
        advance_plan(
            plan_id,
            container.new_unit_of_work(),
            container.agent_runner,
            container.agent_repo,
            container.workspace,
            container.agent_event_sink,
            container.clock,
            handler,
        )
    )
    draft_gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    assert (
        client.post(
            f"/api/plans/{plan_id}/cycle-draft/approve",
            json={
                "gate_id": draft_gate["id"],
                "subject_revision": draft_gate["subject_revision"],
            },
        ).status_code
        == 201
    )
    # One more worker unit persists only the current head goal's contract/tasks.
    asyncio.run(
        advance_plan(
            plan_id,
            container.new_unit_of_work(),
            container.agent_runner,
            container.agent_repo,
            container.workspace,
            container.agent_event_sink,
            container.clock,
            handler,
        )
    )
    return plan_id


def test_pause_resume_and_edit_over_http(client):
    plan_id = _plan_with_enriched_active_goal(client)
    detail = client.get(f"/api/plans/{plan_id}").json()
    assert detail["status"] == "running", detail["block"]
    assert client.post(f"/api/plans/{plan_id}/pause", json={"reason": "hold"}).status_code == 204
    assert client.get(f"/api/plans/{plan_id}").json()["paused"] is True
    goals = client.get(f"/api/plans/{plan_id}").json()["goals"]
    g1 = goals[0]["id"]

    # Paused head-goal work is editable without enriching later goals.
    assert (
        client.post(
            f"/api/plans/{plan_id}/edits",
            json={"type": "update_goal", "goal_id": g1, "name": "G1 renamed"},
        ).status_code
        == 204
    )
    task_id = client.get(f"/api/plans/{plan_id}").json()["goals"][0]["tasks"][0]["id"]
    assert (
        client.post(
            f"/api/plans/{plan_id}/edits",
            json={
                "type": "update_task",
                "goal_id": g1,
                "task_id": task_id,
                "name": "t renamed",
            },
        ).status_code
        == 204
    )
    refreshed = client.get(f"/api/plans/{plan_id}").json()
    assert refreshed["goals"][0]["name"] == "G1 renamed"
    assert refreshed["goals"][0]["tasks"][0]["name"] == "t renamed"

    # Resume clears only the pause gate; a second resume is rejected.
    assert client.post(f"/api/plans/{plan_id}/resume").status_code == 204
    assert client.get(f"/api/plans/{plan_id}").json()["paused"] is False
    assert client.post(f"/api/plans/{plan_id}/resume").status_code == 422


def test_blocked_task_retry_over_http(client):
    plan_id = _plan_with_enriched_active_goal(client)

    from src.domain.entities.planning_artifacts import PlanBlock
    from src.domain.factories.identity import new_id
    from src.domain.value_objects.lifecycle import FailureKind

    container = dependencies.get_container()
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        assert plan.active_cycle is not None
        goal = plan.active_cycle.goals[0]
        task = goal.tasks[0]
        task.fail("terminal authentication failure", FailureKind.AUTH_ERROR)
        plan.open_block(
            PlanBlock(
                id=new_id(),
                kind="execution_failure",
                explanation="terminal authentication failure",
                stage=task.tdd_stage,
                goal_id=goal.id,
                task_id=task.id,
                task_revision=task.revision,
                legal_resolutions=["retry_stage", "edit_task", "start_replan"],
                created_at=container.clock.now(),
            )
        )
        plan.bump_version()
        uow.plans.save(plan)

    blocked = client.get(f"/api/plans/{plan_id}").json()
    assert blocked["status"] == "blocked"
    # Domain unfreeze #14: a cyclic goal's block is exposed via goal_blocks,
    # not the legacy scalar `block` (which stays null for it).
    assert blocked["block"] is None
    goal_block = blocked["goal_blocks"][goal.id]
    assert goal_block["legal_resolutions"] == [
        "retry_stage",
        "edit_task",
        "start_replan",
    ]
    goal_id = goal_block["goal_id"]
    task_id = goal_block["task_id"]

    response = client.post(
        f"/api/plans/{plan_id}/retry",
        json={"goal_id": goal_id, "task_id": task_id},
    )
    assert response.status_code == 204
    recovered = client.get(f"/api/plans/{plan_id}").json()
    assert recovered["status"] == "running"
    assert recovered["block"] is None
    assert recovered["goal_blocks"] == {}
    retried = recovered["goals"][0]["tasks"][0]
    assert retried["status"] == "pending"
    assert retried["retry_cycle"] == 1


def test_blocked_planning_stage_retry_over_http(client):
    plan_id = _plan_with_enriched_active_goal(client)

    from datetime import timedelta

    from src.domain.entities.planning_artifacts import PlanBlock
    from src.domain.factories.identity import new_id

    container = dependencies.get_container()
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        plan.planning_attempts = 3
        plan.planning_retry_not_before = container.clock.now() + timedelta(hours=1)
        plan.open_block(
            PlanBlock(
                id=new_id(),
                kind="reasoner_failure",
                explanation="planner unavailable",
                stage="goal_enrichment",
                legal_resolutions=["retry_stage", "start_replan"],
                created_at=container.clock.now(),
            )
        )
        plan.bump_version()
        uow.plans.save(plan)

    assert client.post(f"/api/plans/{plan_id}/retry-stage").status_code == 204
    recovered = client.get(f"/api/plans/{plan_id}").json()
    assert recovered["status"] == "running"
    assert recovered["block"] is None


def test_pause_unknown_plan_404(client):
    assert client.post("/api/plans/ghost/pause").status_code == 404


def _seed_agent_events(events):
    """Write agent_events rows directly through the sink for read-side tests."""
    import asyncio

    from src.api import dependencies
    from src.domain.events.agent_events import AgentEvent

    sink = dependencies.get_container().agent_event_sink
    for e in events:
        asyncio.run(sink.emit(AgentEvent(**e)))


def test_agent_events_read_endpoint(client):
    plan_id = client.post("/api/plans", json={"brief": "b", "project_id": "project-1"}).json()[
        "plan_id"
    ]
    _seed_agent_events(
        [
            {
                "plan_id": plan_id,
                "task_id": "t1",
                "attempt": 1,
                "seq": 0,
                "type": "agent.started",
                "payload": {"runtime": "pi"},
            },
            {
                "plan_id": plan_id,
                "task_id": "t1",
                "attempt": 1,
                "seq": 1,
                "type": "agent.finished",
                "payload": {"elapsed_seconds": "3.0"},
            },
            {
                "plan_id": plan_id,
                "task_id": "t2",
                "attempt": 1,
                "seq": 0,
                "type": "agent.started",
                "payload": {"runtime": "pi"},
            },
            {
                "plan_id": plan_id,
                "task_id": None,
                "attempt": 0,
                "seq": 0,
                "type": "llm.call",
                "payload": {"total_tokens": "50"},
            },
        ]
    )

    # whole plan, most-recent first
    all_events = client.get(f"/api/plans/{plan_id}/agent-events").json()
    assert [e["type"] for e in all_events][0] == "llm.call"  # newest
    assert len(all_events) == 4
    # the plan-scoped row round-trips a null task_id
    assert any(e["task_id"] is None for e in all_events)

    # filtered to one task
    t1 = client.get(f"/api/plans/{plan_id}/agent-events?task_id=t1").json()
    assert {e["type"] for e in t1} == {"agent.started", "agent.finished"}
    assert all(e["task_id"] == "t1" for e in t1)

    # unknown plan -> 404
    assert client.get("/api/plans/ghost/agent-events").status_code == 404


def test_metrics_endpoint(client):
    plan_id = client.post("/api/plans", json={"brief": "b", "project_id": "project-1"}).json()[
        "plan_id"
    ]
    import asyncio
    from uuid import uuid4
    from src.api import dependencies
    from src.app.execution_records import (
        ExecutionAttempt,
        ExecutionAttemptStatus,
        ExecutionRun,
        ExecutionRunStatus,
    )
    from src.app.observations import (
        ModelUsagePayload,
        ObservationCorrelation,
        ObservationKind,
        ObservationQuality,
        ObservationSource,
        TelemetryObservation,
    )
    from src.app.runtime_failures import RuntimeFailure
    from src.domain.value_objects.lifecycle import FailureKind

    container = dependencies.get_container()
    asyncio.run(
        container.observation_repository.append(
            TelemetryObservation(
                correlation=ObservationCorrelation(plan_id=plan_id),
                observed_at=container.clock.now(),
                source=ObservationSource.PROVIDER,
                quality=ObservationQuality.REPORTED,
                kind=ObservationKind.MODEL_USAGE,
                payload=ModelUsagePayload(
                    model_request_count=2,
                    turn_count=2,
                    input_tokens=30,
                    output_tokens=13,
                    reasoning_tokens=None,
                    cached_tokens=None,
                    total_tokens=43,
                    context="discovery",
                ),
            )
        )
    )
    with container.new_unit_of_work() as uow:
        for index in range(2):
            run_id = str(uuid4())
            attempt_id = str(uuid4())
            uow.executions.add_run(
                ExecutionRun(
                    id=run_id,
                    plan_id=plan_id,
                    goal_id="g1",
                    task_id=f"t{index + 1}",
                    status=ExecutionRunStatus.RUNNING,
                    started_at=container.clock.now(),
                )
            )
            uow.executions.add_attempt(
                ExecutionAttempt(
                    id=attempt_id,
                    run_id=run_id,
                    plan_id=plan_id,
                    goal_id="g1",
                    task_id=f"t{index + 1}",
                    number=1,
                    task_attempt=1,
                    status=ExecutionAttemptStatus.RUNNING,
                    started_at=container.clock.now(),
                )
            )
            uow.executions.finalize_attempt(
                attempt_id,
                attempt_status=ExecutionAttemptStatus.FAILED,
                run_status=ExecutionRunStatus.FAILED,
                completed_at=container.clock.now(),
                failure=RuntimeFailure(
                    kind=FailureKind.RATE_LIMIT,
                    safe_message="capacity",
                    retryable=True,
                ),
            )

    body = client.get("/api/metrics").json()
    assert body["llm"]["scopes"]["planner"]["total_tokens"] == 43
    assert body["llm"]["scopes"]["child"]["total_tokens"] is None
    assert body["llm"]["coverage"]["reported"] == 1
    assert body["agent"]["runs"] == 2
    assert body["agent"]["failed"] == 2
    assert body["agent"]["failures_by_kind"]["rate_limit"] == 2

    timeline = client.get(f"/api/plans/{plan_id}/attempts")
    assert timeline.status_code == 200
    timeline_body = timeline.json()
    assert [task["task_id"] for task in timeline_body["tasks"]] == ["t1", "t2"]
    attempts = [
        attempt
        for task in timeline_body["tasks"]
        for run in task["runs"]
        for attempt in run["attempts"]
    ]
    assert all(attempt["failure_kind"] == "rate_limit" for attempt in attempts)
    assert all(attempt["safe_message"] == "capacity" for attempt in attempts)

    # per-plan filter narrows the same way (unknown plan -> zeros)
    empty = client.get("/api/metrics?plan_id=ghost").json()
    assert empty["llm"]["total_tokens"] is None


def test_error_mapping_table(client):
    # 404 PLAN_NOT_FOUND
    missing = client.get("/api/plans/ghost")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PLAN_NOT_FOUND"
    assert missing.json()["error"]["request_id"]

    # 422 EMPTY_PLAN (birth invariant)
    empty = client.post("/api/plans", json={"brief": "   ", "project_id": "project-1"})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "EMPTY_PLAN"

    # 422 INVALID_TRANSITION (approve a plan that isn't at the gate)
    plan_id = client.post("/api/plans", json={"brief": "b", "project_id": "project-1"}).json()[
        "plan_id"
    ]
    bad_approve = client.post(f"/api/plans/{plan_id}/approve")
    assert bad_approve.status_code == 422
    assert bad_approve.json()["error"]["code"] == "INVALID_TRANSITION"

    # 422 UNKNOWN_CAPABILITY via the edit boundary — but on a goal that exists
    # requires a plan with goals; simpler: 404 CAPABILITY_NOT_FOUND
    missing_cap = client.delete("/api/capabilities/ghost")
    assert missing_cap.status_code == 404
    assert missing_cap.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"

    # 409 ENTITY_ALREADY_EXISTS
    cap = {"id": "c1", "name": "c", "description": "", "tools": []}
    assert client.post("/api/capabilities", json=cap).status_code == 201
    dup = client.post("/api/capabilities", json=cap)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ENTITY_ALREADY_EXISTS"


def test_provider_secrets_never_echoed_or_stored_plaintext(client, tmp_path):
    created = client.post(
        "/api/providers",
        json={"name": "P", "base_url": "https://api", "api_key": "sk-super-secret"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["api_key_ref"].startswith("secret://provider/")
    assert "sk-super-secret" not in created.text

    listing = client.get("/api/providers")
    assert "sk-super-secret" not in listing.text

    # at rest: only ciphertext
    container = dependencies.get_container()
    with container.session_factory() as s:
        rows = s.execute(text("SELECT ciphertext FROM secrets")).all()
    assert rows and all("sk-super-secret" not in r[0] for r in rows)


def test_model_rename_over_http(client):
    provider_id = client.post(
        "/api/providers",
        json={"name": "P", "base_url": "https://api", "api_key": "sk-1"},
    ).json()["id"]
    model = client.post(f"/api/providers/{provider_id}/models", json={"name": "gpt-old"}).json()

    renamed = client.put(f"/api/models/{model['id']}", json={"name": "gpt-new"})
    assert renamed.status_code == 204
    listing = {m["id"]: m for m in client.get("/api/models").json()}
    assert listing[model["id"]]["name"] == "gpt-new"
    assert listing[model["id"]]["provider_id"] == provider_id

    ghost = client.put("/api/models/ghost", json={"name": "x"})
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "MODEL_NOT_FOUND"


def test_reasoner_status_walk_over_http(client):
    # fresh DB: stub default, always valid
    fresh = client.get("/api/reasoner/status")
    assert fresh.status_code == 200
    assert fresh.json()["mode"] == "stub"
    assert fresh.json()["valid"] is True

    # llm mode without a provider: invalid with the actionable detail, still 200
    client.put("/api/config/orchestrator/reasoner.mode", json={"value": "llm"})
    unset = client.get("/api/reasoner/status").json()
    assert unset["valid"] is False
    assert "reasoner.provider_id" in unset["detail"]

    # wire a real provider + model over HTTP -> valid, names resolved
    provider = client.post(
        "/api/providers",
        json={"name": "OpenRouter", "base_url": "https://or", "api_key": "sk-1"},
    ).json()
    model = client.post(f"/api/providers/{provider['id']}/models", json={"name": "gpt-x"}).json()
    client.put(
        "/api/config/orchestrator/reasoner.provider_id",
        json={"value": provider["id"]},
    )
    client.put("/api/config/orchestrator/reasoner.model_id", json={"value": model["id"]})
    wired = client.get("/api/reasoner/status").json()
    assert wired == {
        "mode": "llm",
        "valid": True,
        "detail": None,
        "provider_id": provider["id"],
        "provider_name": "OpenRouter",
        "model_id": model["id"],
        "model_name": "gpt-x",
    }

    # a model from a different provider: invalid cross-provider wiring
    other = client.post(
        "/api/providers",
        json={"name": "Other", "base_url": "https://o", "api_key": "sk-2"},
    ).json()
    stray = client.post(f"/api/providers/{other['id']}/models", json={"name": "m"}).json()
    client.put("/api/config/orchestrator/reasoner.model_id", json={"value": stray["id"]})
    crossed = client.get("/api/reasoner/status").json()
    assert crossed["valid"] is False
    assert "belongs to provider" in crossed["detail"]


def test_runner_status_walk_over_http(client):
    # fresh DB: dry-run default, always valid; binaries reported informatively
    fresh = client.get("/api/runner/status")
    assert fresh.status_code == 200
    body = fresh.json()
    assert body["mode"] == "dry-run"
    assert body["valid"] is True
    assert {b["name"] for b in body["binaries"]} == {"git", "pi", "claude", "gemini"}
    assert body["agents"] == []
    # ROADMAP item 33: NoSandbox reports honestly, never as a healthy sandbox
    assert body["sandbox"]["ok"] is True
    assert "disabled" in body["sandbox"]["message"].lower()

    # real mode with an unbound pi agent: invalid with the agent's detail
    agent = client.post(
        "/api/agents",
        json={"name": "A", "role": "implementer", "model_role": "smart"},
    ).json()
    client.put("/api/config/orchestrator/agent_runner.mode", json={"value": "real"})
    unbound = client.get("/api/runner/status").json()
    assert unbound["mode"] == "real"
    assert unbound["valid"] is False
    assert "no provider_id" in unbound["detail"]
    assert unbound["agents"][0]["runtime_type"] == "pi"

    # bind through the catalog (provider named after a pi backend) -> valid
    provider = client.post(
        "/api/providers",
        json={"name": "anthropic", "base_url": "https://a", "api_key": "sk-1"},
    ).json()
    model = client.post(f"/api/providers/{provider['id']}/models", json={"name": "sonnet"}).json()
    assert (
        client.put(
            f"/api/agents/{agent['id']}",
            json={
                "name": "A",
                "role": "implementer",
                "model_role": "smart",
                "runtime_type": "claude",
                "provider_id": provider["id"],
                "model_id": model["id"],
            },
        ).status_code
        == 204
    )
    bound = client.get("/api/runner/status").json()
    assert bound["valid"] is True
    assert bound["agents"][0]["provider_name"] == "anthropic"
    assert bound["agents"][0]["model_name"] == "sonnet"

    # a bound provider/model is delete-guarded
    assert client.delete(f"/api/models/{model['id']}").status_code == 409
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 409


def test_agent_runtime_write_validation_over_http(client):
    bad_runtime = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "runtime_type": "cobol",
        },
    )
    assert bad_runtime.status_code == 422
    assert bad_runtime.json()["error"]["code"] == "AGENT_RUNNER_CONFIG_INVALID"

    ghost_provider = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "provider_id": "ghost",
        },
    )
    assert ghost_provider.status_code == 404

    provider = client.post(
        "/api/providers",
        json={"name": "P1", "base_url": "https://p1", "api_key": "k1"},
    ).json()
    other = client.post(
        "/api/providers",
        json={"name": "P2", "base_url": "https://p2", "api_key": "k2"},
    ).json()
    stray = client.post(f"/api/providers/{other['id']}/models", json={"name": "m"}).json()
    crossed = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "provider_id": provider["id"],
            "model_id": stray["id"],
        },
    )
    assert crossed.status_code == 422
    assert "belongs to provider" in crossed.json()["error"]["message"]


def test_default_agent_read_over_http(client):
    assert client.get("/api/agents/default").json() == {"agent_id": None}
    agent_id = client.post(
        "/api/agents",
        json={"name": "A", "role": "implementer", "model_role": "smart"},
    ).json()["id"]
    client.post(f"/api/agents/{agent_id}/default")
    assert client.get("/api/agents/default").json() == {"agent_id": agent_id}


def test_agents_and_default_marker_over_http(client):
    cap = {"id": "backend", "name": "backend", "description": "", "tools": []}
    client.post("/api/capabilities", json=cap)
    agent = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "capability_ids": ["backend"],
        },
    )
    assert agent.status_code == 201
    agent_id = agent.json()["id"]
    assert agent.json()["capabilities"][0]["id"] == "backend"
    assert client.post(f"/api/agents/{agent_id}/default").status_code == 204


def test_two_tier_config_over_http(client):
    assert (
        client.put("/api/config/orchestrator/poll_seconds", json={"value": "2"}).status_code == 204
    )
    assert client.put("/api/config/proj-1/framework", json={"value": "fastapi"}).status_code == 204
    assert client.get("/api/config/orchestrator").json() == {"poll_seconds": "2"}
    assert client.get("/api/config/proj-1").json() == {"framework": "fastapi"}


def test_control_plane_token_guard(client, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    denied = client.get("/api/providers")
    assert denied.status_code == 401
    allowed = client.get("/api/providers", headers={"Authorization": "Bearer sekrit"})
    assert allowed.status_code == 200


def _plan_at_cycle_review(client) -> str:
    project_id = client.post("/api/projects", json={"name": "Cycle project"}).json()["id"]
    plan_id = client.post("/api/plans", json={"brief": "cycle", "project_id": project_id}).json()["plan_id"]
    client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": "Deliver cycle"})
    gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    assert client.post(f"/api/plans/{plan_id}/intent/approve", json={"gate_id": gate["id"], "subject_revision": gate["subject_revision"]}).status_code == 204
    return plan_id


def _set_plan_phase(plan_id: str, phase) -> None:
    container = dependencies.get_container()
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        plan._set_phase(phase)
        plan.bump_version()
        uow.plans.save(plan)


def test_plan_intent_and_cycle_draft_cancel_routes_over_http(client):
    from src.domain.aggregates.planner_orchestrator import PlanPhase

    plan_id = client.post("/api/plans", json={"brief": "b", "project_id": "project-1"}).json()["plan_id"]
    client.post(f"/api/plans/{plan_id}/intent", json={"objective": "o"})
    assert client.delete(f"/api/plans/{plan_id}/intent").status_code == 204
    denied = client.delete(f"/api/plans/{plan_id}/intent")
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "INVALID_EDIT"

    draft_plan = _plan_at_cycle_review(client)
    _set_plan_phase(draft_plan, PlanPhase.ARCHITECTURE)
    assert client.post(f"/api/plans/{draft_plan}/cycle-draft", json={"goals": [{"key": "g", "name": "G", "objective": "g", "position": 0, "depends_on": []}]}).status_code == 201
    _set_plan_phase(draft_plan, PlanPhase.AWAITING_REVIEW)
    assert client.delete(f"/api/plans/{draft_plan}/cycle-draft").status_code == 204
    denied = client.delete(f"/api/plans/{draft_plan}/cycle-draft")
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "INVALID_EDIT"


def test_review_reopen_route_over_http(client):
    from src.domain.aggregates.planner_orchestrator import PlanPhase
    plan_id = _plan_at_cycle_review(client)
    assert client.post(f"/api/plans/{plan_id}/cycle-draft", json={"goals": [{"key": "g", "name": "G", "objective": "g", "position": 0, "depends_on": []}]}).status_code == 201
    _set_plan_phase(plan_id, PlanPhase.AWAITING_REVIEW)
    assert client.post(f"/api/plans/{plan_id}/review/reopen").status_code == 204
    assert client.get(f"/api/plans/{plan_id}").json()["phase"] == PlanPhase.DISCOVERY.value
    denied = client.post(f"/api/plans/{plan_id}/review/reopen")
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "INVALID_TRANSITION"


def test_review_finish_and_replan_routes_over_http(client):
    from src.domain.aggregates.planner_orchestrator import PlanPhase
    finish_plan = _plan_with_enriched_active_goal(client)
    _set_plan_phase(finish_plan, PlanPhase.REVIEW)
    assert client.post(f"/api/plans/{finish_plan}/review/finish").status_code == 204
    assert client.get(f"/api/plans/{finish_plan}").json()["phase"] == PlanPhase.DONE.value
    # Re-finishing a DONE plan is rejected by the transition guard. (The
    # dedicated PLAN_ALREADY_TERMINAL code is not naturally reachable over
    # HTTP: the phase-transition guard fires first.)
    terminal = client.post(f"/api/plans/{finish_plan}/review/finish")
    assert terminal.status_code == 422
    assert terminal.json()["error"]["code"] == "INVALID_TRANSITION"
    replan_project = client.post("/api/projects", json={"name": "Replan project"}).json()
    review_replan = _plan_with_enriched_active_goal(client, replan_project["id"])
    _set_plan_phase(review_replan, PlanPhase.REVIEW)
    assert client.post(f"/api/plans/{review_replan}/review/replan").status_code == 204
    assert client.get(f"/api/plans/{review_replan}").json()["phase"] == PlanPhase.REPLANNING.value
    # Replan requests are safe to repeat while conversational replanning is
    # already active; the coherent WAITING tuple is simply re-established.
    repeated = client.post(f"/api/plans/{review_replan}/review/replan")
    assert repeated.status_code == 204
    assert client.get(f"/api/plans/{review_replan}").json()["phase"] == PlanPhase.REPLANNING.value


def test_mid_running_replan_and_replanning_message_routes_over_http(client):
    from src.domain.aggregates.planner_orchestrator import PlanPhase
    plan_id = _plan_with_enriched_active_goal(client)
    assert client.post(f"/api/plans/{plan_id}/replan").status_code == 204
    assert client.get(f"/api/plans/{plan_id}").json()["phase"] == PlanPhase.REPLANNING.value
    container = dependencies.get_container()
    from src.domain.entities.planning_artifacts import PlanStatus
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(plan_id)
        plan.status = PlanStatus.IDLE
        plan.bump_version()
        uow.plans.save(plan)
    committed = client.post(f"/api/plans/{plan_id}/replanning/message", json={"message": ""})
    assert committed.status_code == 200
    assert committed.json()["committed"] is True
    assert committed.json()["phase"] == PlanPhase.REPLANNING.value
    # Re-entering replanning is idempotent at the HTTP boundary and retires the
    # just-created proposal so discovery can restart cleanly.
    repeated = client.post(f"/api/plans/{plan_id}/replan")
    assert repeated.status_code == 204
    restarted = client.post(f"/api/plans/{plan_id}/replanning/message", json={"message": "x"})
    assert restarted.status_code == 200
    assert restarted.json()["committed"] is True
    assert restarted.json()["phase"] == PlanPhase.REPLANNING.value


def test_additional_error_codes_over_http(client, monkeypatch):
    plan_id = _plan_with_enriched_active_goal(client)
    goal_id = client.get(f"/api/plans/{plan_id}").json()["goals"][0]["id"]

    missing_goal = client.post(f"/api/plans/{plan_id}/edits", json={"type": "update_goal", "goal_id": "ghost", "name": "x"})
    assert missing_goal.status_code == 404
    assert missing_goal.json()["error"]["code"] == "GOAL_NOT_FOUND"
    missing_task = client.post(f"/api/plans/{plan_id}/edits", json={"type": "update_task", "goal_id": goal_id, "task_id": "ghost", "name": "x"})
    assert missing_task.status_code == 404
    assert missing_task.json()["error"]["code"] == "TASK_NOT_FOUND"

    missing_agent = client.delete("/api/agents/ghost")
    assert missing_agent.status_code == 404
    assert missing_agent.json()["error"]["code"] == "AGENT_NOT_FOUND"
    missing_provider = client.delete("/api/providers/ghost")
    assert missing_provider.status_code == 404
    assert missing_provider.json()["error"]["code"] == "PROVIDER_NOT_FOUND"

    invalid_edit = client.post(f"/api/plans/{plan_id}/edits", json={"type": "rebind_task_agent", "goal_id": goal_id, "task_id": "ghost"})
    assert invalid_edit.status_code == 422
    assert invalid_edit.json()["error"]["code"] == "INVALID_EDIT"

    from src.domain.errors.tasks_errors import StaleVersionError
    from src.infra.db.plan_repository import SqlitePlanRepository
    original_save = SqlitePlanRepository.save
    monkeypatch.setattr(SqlitePlanRepository, "save", lambda self, plan: (_ for _ in ()).throw(StaleVersionError(plan.id, plan.version, plan.version + 1)))
    stale = client.post(f"/api/plans/{plan_id}/edits", json={"type": "update_goal", "goal_id": goal_id, "name": "x"})
    monkeypatch.setattr(SqlitePlanRepository, "save", original_save)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_VERSION"


def test_agents_and_projects_crud_over_http(client):
    agent = client.post("/api/agents", json={"name": "A", "role": "implementer", "model_role": "smart"})
    assert agent.status_code == 201
    agent_id = agent.json()["id"]
    assert any(item["id"] == agent_id for item in client.get("/api/agents").json())
    assert client.put(f"/api/agents/{agent_id}", json={"name": "A2", "role": "implementer", "model_role": "smart"}).status_code == 204
    assert any(item["name"] == "A2" for item in client.get("/api/agents").json())
    assert client.delete(f"/api/agents/{agent_id}").status_code == 204
    assert all(item["id"] != agent_id for item in client.get("/api/agents").json())

    project = client.post("/api/projects", json={"name": "P", "repo_url": "https://repo"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    assert any(item["id"] == project_id for item in client.get("/api/projects").json())
    assert client.put(f"/api/projects/{project_id}", json={"name": "P2", "repo_url": None}).status_code == 204
    assert next(item for item in client.get("/api/projects").json() if item["id"] == project_id)["name"] == "P2"
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert all(item["id"] != project_id for item in client.get("/api/projects").json())
