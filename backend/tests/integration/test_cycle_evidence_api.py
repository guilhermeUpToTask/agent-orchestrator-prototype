"""G9's objective test: one evidence read model per cycle, asserted against a
completed dry-run cycle."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def evidence_client(tmp_path, monkeypatch):
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(tmp_path, monkeypatch)
    return walk.client, walk.plan_id, walk.cycle_id


def test_completed_cycle_serves_all_four_evidence_facts(evidence_client) -> None:
    client, plan_id, cycle_id = evidence_client

    response = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == plan_id
    assert body["cycle_id"] == cycle_id
    assert body["goals"], "a completed cycle has goals"

    goal = body["goals"][0]
    # 1. promoted refs
    assert goal["promotion"]["into_ref"] == f"cycle/{cycle_id}"
    assert goal["promotion"]["from_ref"] == f"goal/{goal['goal_id']}"
    assert goal["promotion"]["merge_sha"]

    task = goal["tasks"][0]
    # 2. protected scope, both halves joined
    assert "allowed_scope" in task["protected_scope"]
    assert "forbidden_scope" in task["protected_scope"]
    assert "protected_file_hashes" in task["protected_scope"]
    # 3. accepted evidence
    assert task["accepted_evidence"], "a done task has accepted evidence"
    assert all(item["exit_code"] == 0 for item in task["accepted_evidence"])
    # 4. disposition
    assert body["disposition"]["disposition"] in {
        "open_pr",
        "merge",
        "retain_branch",
        "discard",
    }


def test_unknown_cycle_is_404(evidence_client) -> None:
    client, plan_id, _ = evidence_client
    response = client.get(f"/api/plans/{plan_id}/cycles/nope/evidence")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Edge-case fixtures — the six places a read model can lie if it isn't careful.
# ---------------------------------------------------------------------------


@pytest.fixture
def evidence_walk(tmp_path, monkeypatch):
    """The whole `CompletedCycle`, not the 3-tuple `evidence_client` narrows
    it to: the migration-straddling and pre-0017 tests need `.container.engine`
    for direct SQL against `goal_promotions`."""
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    return drive_cycle_to_publication(tmp_path, monkeypatch)


@pytest.fixture
def other_plan(evidence_client):
    """A second, unrelated plan in the SAME container as `evidence_client`.

    Posting to `evidence_client`'s own project would just reopen the SAME
    plan -- a project owns exactly one long-lived Plan -- so this gives
    itself a fresh scratch-repo project first. It needs no cycle; the point
    is only that its id must not serve another plan's cycle.
    """
    client, _, _ = evidence_client

    project = client.post("/api/projects", json={"name": "Unrelated project"})
    assert project.status_code == 201, project.text

    response = client.post(
        "/api/plans",
        json={"brief": "an unrelated plan", "project_id": project.json()["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["plan_id"]


@pytest.fixture
def evidence_client_with_edit(tmp_path, monkeypatch):
    """A task that already has accepted evidence, edited through the real
    `POST /edits` route so its revision bumps and the prior evidence goes
    stale.

    `Task.semantic_edit` (frozen domain) invalidates a revision by CLEARING
    `verification_evidence` outright rather than leaving the old items
    attached at their original revision -- there is no in-domain path that
    produces "accepted, but revision-mismatched" evidence on its own; a
    second real run at the new revision would REPLACE the list too, not
    accumulate it. This fixture drives the edit for real (so the route, the
    permission checks, and the revision bump are all genuine), then
    reinstates the evidence the walk actually produced, at its original
    revision, so the assertions exercise the endpoint's own
    revision-mismatch filter against real (if reinstated) data instead of a
    fabricated shape.
    """
    from src.domain.value_objects.lifecycle import Status
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
        goal = plan.active_cycle.goals[0]
        task = goal.tasks[0]
        goal_id, task_id = goal.id, task.id
        assert task.verification_evidence, "the walk must leave accepted evidence"
        stale_evidence = list(task.verification_evidence)
        stale_revision = task.revision

        # `edit_service._assert_editable` refuses a DONE (terminal) goal
        # outright, and `semantic_edit` is only reachable from PENDING/FAILED
        # -- there is no wired use case for "redo a promoted task" (the
        # aggregate exposes `reopen_task`, but nothing in `src/app` calls
        # it), and the plan cannot pause out of its completion-review WAITING
        # status either. This is fixture setup, not application code, so
        # reaching in directly is in bounds here -- the same technique the
        # migration tests below use raw SQL for: reaching a state the routes
        # alone cannot construct.
        goal.status = Status.PENDING
        task.status = Status.PENDING
        plan.bump_version()
        uow.plans.save(plan)

    response = walk.client.post(
        f"/api/plans/{walk.plan_id}/edits",
        json={
            "type": "update_task",
            "goal_id": goal_id,
            "task_id": task_id,
            "name": "renamed after promotion",
        },
    )
    assert response.status_code == 204, response.text

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
        goal = next(g for g in plan.active_cycle.goals if g.id == goal_id)
        task = next(t for t in goal.tasks if t.id == task_id)
        assert task.revision == stale_revision + 1
        task.verification_evidence = [
            item.model_copy(update={"task_revision": stale_revision})
            for item in stale_evidence
        ]
        plan.bump_version()
        uow.plans.save(plan)

    return walk.client, walk.plan_id, walk.cycle_id, task_id


@pytest.fixture
def discarded_cycle_client(tmp_path, monkeypatch):
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(
        tmp_path, monkeypatch, disposition="discard", output_reference=None
    )
    return walk.client, walk.plan_id, walk.cycle_id


@pytest.fixture
def replanned_client(tmp_path, monkeypatch):
    """Drive one cycle to its completion-review gate (not published), then
    replan mid-review: a new intent -> a new CycleDraft -> activation, which
    atomically supersedes the source cycle. Returns the SOURCE cycle id --
    the whole point of keying this endpoint on cycle id rather than plan id
    is that its evidence stays addressable afterward.
    """
    import asyncio

    from src.app.handlers.planning_handler import PlanningHandler
    from src.app.use_cases.run_worker import drive_plan
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)
    client = walk.client
    plan_id = walk.plan_id
    source_cycle_id = walk.cycle_id
    container = walk.container

    replan_response = client.post(f"/api/plans/{plan_id}/replan")
    assert replan_response.status_code == 204, replan_response.text

    intent_response = client.post(
        f"/api/plans/{plan_id}/intent",
        json={
            "objective": "deliver a replacement dry-run cycle",
            "scope": ["."],
            "constraints": ["deterministic"],
            "exclusions": [],
            "kind": "replan",
        },
    )
    assert intent_response.status_code == 201, intent_response.text
    proposal = intent_response.json()

    detail = client.get(f"/api/plans/{plan_id}").json()
    intent_gate = detail["pending_gate"]
    assert intent_gate is not None and intent_gate["subject_type"] == "intent"

    approve_response = client.post(
        f"/api/plans/{plan_id}/intent/approve",
        json={"gate_id": intent_gate["id"], "subject_revision": proposal["revision"]},
    )
    assert approve_response.status_code == 204, approve_response.text

    planning = PlanningHandler(
        container.reasoner, container.agent_repo, container.capability_repo, container.clock
    )

    async def drive() -> tuple[str, int]:
        return await drive_plan(
            plan_id,
            container.new_unit_of_work(),
            container.agent_runner,
            container.agent_repo,
            container.workspace,
            container.agent_event_sink,
            container.clock,
            "worker-1",
            planning_handler=planning,
            verifier=container.verification_executor,
        )

    architecture_signal, _ = asyncio.run(drive())
    assert architecture_signal == "paused"

    detail = client.get(f"/api/plans/{plan_id}").json()
    draft_gate = detail["pending_gate"]
    assert draft_gate is not None and draft_gate["subject_type"] == "cycle_draft"
    assert detail["cycle_draft"] is not None

    activate_response = client.post(
        f"/api/plans/{plan_id}/cycle-draft/approve",
        json={
            "gate_id": draft_gate["id"],
            "subject_revision": detail["cycle_draft"]["revision"],
        },
    )
    assert activate_response.status_code == 201, activate_response.text

    return client, plan_id, source_cycle_id


def test_edited_task_stops_serving_its_stale_evidence_as_accepted(
    evidence_client_with_edit,
) -> None:
    """`edit_task` invalidates revision-bound evidence. Serving it as accepted
    would make the endpoint claim the current contract is satisfied when it is
    not."""
    client, plan_id, cycle_id, task_id = evidence_client_with_edit

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    task = next(
        item
        for goal in body["goals"]
        for item in goal["tasks"]
        if item["task_id"] == task_id
    )

    assert task["accepted_evidence"] == []
    assert task["superseded_evidence_count"] >= 1


def test_cycle_id_from_another_plan_is_refused_not_served_empty(
    evidence_client, other_plan
) -> None:
    client, _, cycle_id = evidence_client
    response = client.get(f"/api/plans/{other_plan}/cycles/{cycle_id}/evidence")
    assert response.status_code == 404


def test_discarded_cycle_serves_its_disposition_with_no_reference(
    discarded_cycle_client,
) -> None:
    client, plan_id, cycle_id = discarded_cycle_client
    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    assert body["disposition"]["disposition"] == "discard"
    assert body["disposition"]["output_reference"] is None


def test_pre_0017_cycle_serves_unattributed_refs(evidence_walk) -> None:
    """A cycle promoted before migration 0017 has SHAs in Cycle.evidence_refs
    and no promotion rows. It must say so rather than look unpromoted."""
    from sqlalchemy import text

    client, plan_id, cycle_id = (
        evidence_walk.client,
        evidence_walk.plan_id,
        evidence_walk.cycle_id,
    )

    # Simulate the pre-migration state: drop this cycle's promotion rows.
    # The container is the one the walk built and injected via set_container;
    # there is no `app.state.container` in this codebase.
    with evidence_walk.container.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM goal_promotions WHERE cycle_id = :cycle_id"),
            {"cycle_id": cycle_id},
        )

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    assert body["unattributed_evidence_refs"], "the git:<sha> entries survive"
    assert all(goal["promotion"] is None for goal in body["goals"])


def test_cycle_straddling_the_migration_reports_only_the_unmatched_refs(
    evidence_walk,
) -> None:
    """Exactly one cycle per install can have goals promoted before migration
    0017 (ref, no row) and after it (both). A presence check would return [] and
    hide the pre-migration half."""
    from sqlalchemy import text

    client, plan_id, cycle_id = (
        evidence_walk.client,
        evidence_walk.plan_id,
        evidence_walk.cycle_id,
    )

    with evidence_walk.container.engine.begin() as connection:
        # Orphan exactly one promotion's ref. Works whether the walk promoted
        # one goal or several -- do not assume a goal count here.
        orphaned = connection.execute(
            text(
                "SELECT merge_sha FROM goal_promotions "
                "WHERE cycle_id = :cycle_id ORDER BY promoted_at LIMIT 1"
            ),
            {"cycle_id": cycle_id},
        ).scalar_one()
        connection.execute(
            text(
                "DELETE FROM goal_promotions "
                "WHERE cycle_id = :cycle_id AND merge_sha = :sha"
            ),
            {"cycle_id": cycle_id, "sha": orphaned},
        )
        survivors = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT merge_sha FROM goal_promotions WHERE cycle_id = :cycle_id"
                ),
                {"cycle_id": cycle_id},
            ).all()
        ]

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    unattributed = body["unattributed_evidence_refs"]
    assert f"git:{orphaned}" in unattributed, "the pre-migration half is shown"
    for sha in survivors:
        assert f"git:{sha}" not in unattributed, "an attributed ref is not repeated"


def test_superseded_cycle_still_serves_its_evidence(replanned_client) -> None:
    """Replan is source-preserving: the source cycle stays visible and
    immutable. Its evidence must remain addressable after a new cycle
    activates -- which is the whole reason this endpoint is keyed on cycle id
    rather than plan id."""
    client, plan_id, source_cycle_id = replanned_client

    response = client.get(f"/api/plans/{plan_id}/cycles/{source_cycle_id}/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["cycle_id"] == source_cycle_id
    assert body["cycle_status"] == "superseded"
    assert any(
        task["accepted_evidence"]
        for goal in body["goals"]
        for task in goal["tasks"]
    ), "the source cycle's accepted evidence survives the replan"
