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
    """A task with real accepted evidence, still reachable through its
    active (unpublished) cycle -- no manipulation, no reopen. `publish=False`
    keeps `active_cycle` populated so `edit_task` can be attempted against
    the real task/goal ids for real, whatever the outcome turns out to be.
    """
    from tests.integration.cyclic_walk import drive_cycle_to_publication

    walk = drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)

    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
        goal = plan.active_cycle.goals[0]
        task = goal.tasks[0]
        goal_id, task_id = goal.id, task.id

    return walk.client, walk.plan_id, walk.cycle_id, goal_id, task_id


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

    from praxis_orchestrator.app.handlers.planning_handler import PlanningHandler
    from praxis_orchestrator.app.use_cases.run_worker import drive_plan
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
            container.execution_services,
            "worker-1",
            planning_handler=planning,
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


def test_edited_task_no_longer_serves_its_prior_evidence_as_accepted(
    evidence_client_with_edit,
) -> None:
    """A task's semantic fields (`update_task` / `update_task_contract`) can
    only be edited while it is PENDING, or FAILED while the plan is paused
    (`edit_service._assert_task_mutable`) -- DONE is history. Accepting
    verification evidence and completing a task happen atomically
    (`ExecutionHandler` calls `task.accept_verification` immediately
    followed by `plan.complete_task`), and there is no wired use case that
    reopens a DONE task for editing (`Plan.reopen_task` exists on the
    aggregate but nothing in `praxis_orchestrator/app` calls it) -- nor can the plan pause
    its way out of a completion-review WAITING status. So a task that has
    ever served accepted evidence can never be edited again through the real
    API: `edit_task` refuses it outright, which is the fully honest way the
    read model avoids ever claiming a stale contract is satisfied -- the
    edit that would invalidate the evidence never happens in the first
    place.
    """
    client, plan_id, cycle_id, goal_id, task_id = evidence_client_with_edit

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    task = next(
        item
        for goal in body["goals"]
        for item in goal["tasks"]
        if item["task_id"] == task_id
    )
    assert task["accepted_evidence"], "precondition: the task has accepted evidence"

    response = client.post(
        f"/api/plans/{plan_id}/edits",
        json={
            "type": "update_task",
            "goal_id": goal_id,
            "task_id": task_id,
            "name": "renamed after promotion",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GOAL_ALREADY_RUNNING"

    # The refused edit must not have touched anything: the same evidence is
    # still served as accepted.
    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    task = next(
        item
        for goal in body["goals"]
        for item in goal["tasks"]
        if item["task_id"] == task_id
    )
    assert task["accepted_evidence"], "a refused edit must not clear accepted evidence"


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


# ---------------------------------------------------------------------------
# Delivery: where the work actually is. `ProjectDefinition.repo_url` decides
# three topologies and they need three different answers -- the UI used to give
# all three the same one ("push refs/heads/cycle/<id> and open a pull request"),
# which is advice a REMOTE-bound operator cannot follow, because the branch is
# in a clone the orchestrator owns and they have never seen.
# ---------------------------------------------------------------------------


def _rebind(walk, repo_url: str | None) -> None:
    """Repoint the completed walk's project at another topology.

    The delivery block is derived per request from the project row and the
    cycle id -- it does not depend on how the cycle executed -- so rebinding
    afterwards exercises the real endpoint for all three topologies without
    paying for three full cycle drives.
    """
    from praxis_orchestrator.domain.entities.project_definition import ProjectDefinition

    project = walk.container.project_repo.get("project-1")
    walk.container.project_repo.update(
        ProjectDefinition(id=project.id, name=project.name, repo_url=repo_url)
    )


def test_local_binding_reports_the_operators_own_checkout(evidence_walk) -> None:
    body = evidence_walk.client.get(
        f"/api/plans/{evidence_walk.plan_id}"
        f"/cycles/{evidence_walk.cycle_id}/evidence"
    ).json()

    delivery = body["delivery"]
    assert delivery["binding"] == "local"
    assert delivery["repository_path"] == str(evidence_walk.repo)
    assert delivery["in_operator_checkout"] is True
    assert delivery["cycle_branch"] == f"cycle/{evidence_walk.cycle_id}"
    # `trunk`, not `main`: proves the branch is probed on disk rather than
    # assumed, so the diff command the operator is handed actually resolves.
    assert delivery["default_branch"] == "trunk"


def test_remote_binding_does_not_point_at_a_checkout_that_lacks_the_branch(
    evidence_walk,
) -> None:
    """The regression this whole read model exists for.

    A remote-bound project's cycle branch lives ONLY inside
    `$PRAXIS_HOME/projects/<id>/repos/<sha256[:16]>`. Telling that
    operator to push it from their own checkout is a dead end, so the endpoint
    must name the orchestrator's clone and say plainly that it is not theirs.
    """
    _rebind(evidence_walk, "https://example.test/acme/widgets.git")

    body = evidence_walk.client.get(
        f"/api/plans/{evidence_walk.plan_id}"
        f"/cycles/{evidence_walk.cycle_id}/evidence"
    ).json()

    delivery = body["delivery"]
    assert delivery["binding"] == "remote"
    assert delivery["in_operator_checkout"] is False
    assert delivery["repository_path"] != str(evidence_walk.repo)
    assert "/repos/" in delivery["repository_path"], "the orchestrator-owned clone"
    assert delivery["cycle_branch"] == f"cycle/{evidence_walk.cycle_id}"


def test_scratch_binding_is_named_rather_than_dressed_up_as_a_repository(
    evidence_walk,
) -> None:
    _rebind(evidence_walk, None)

    delivery = evidence_walk.client.get(
        f"/api/plans/{evidence_walk.plan_id}"
        f"/cycles/{evidence_walk.cycle_id}/evidence"
    ).json()["delivery"]

    assert delivery["binding"] == "scratch"
    assert delivery["in_operator_checkout"] is False


def test_a_repository_deleted_after_the_run_still_reports_where_it_was(
    evidence_walk,
) -> None:
    """`validate_repo_url` raises for a local path that has since vanished. A
    delivery block that 500s at that point is worse than one that says where
    the work was written -- the operator needs the path precisely because
    something is wrong with it."""
    import shutil

    shutil.rmtree(evidence_walk.repo)

    response = evidence_walk.client.get(
        f"/api/plans/{evidence_walk.plan_id}"
        f"/cycles/{evidence_walk.cycle_id}/evidence"
    )

    assert response.status_code == 200
    delivery = response.json()["delivery"]
    assert delivery["repository_path"] == str(evidence_walk.repo)
    assert delivery["default_branch"] is None, "nothing to probe once it is gone"


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


def test_the_evidence_says_the_tests_were_proven_failing_first(evidence_client) -> None:
    """The claim the product rests on, in the document a sceptic reads.

    The orchestrator already ran the checks after the test-authoring stage and
    already REFUSED to freeze a bundle whose baseline was not failing
    (`app/verification.py::baseline_outcome`; `tdd` and `executable_check` both
    require red). But the verdict lived only in a `verification_baseline`
    planning artifact, so the cycle evidence — the thing published in
    `demos/*/runs/*/evidence.json` — could show `exit_code: 0` for every task and
    say nothing about the failure that came first.

    That made "the tests were proven failing before the implementation" a claim
    a reader could ask for and we could not produce from the evidence. Phase 10B
    surfaced it here.
    """
    client, plan_id, cycle_id = evidence_client

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    bundles = [
        task["test_bundle"]
        for goal in body["goals"]
        for task in goal["tasks"]
        if task["test_bundle"] is not None
    ]
    assert bundles, "a completed cycle froze at least one test bundle"

    for bundle in bundles:
        baseline = bundle["baseline"]
        assert baseline is not None, "a frozen bundle carries the baseline it was accepted on"
        assert baseline["verdict"] == "red", (
            "the checks must have FAILED before the implementation existed — a "
            f"green baseline means the later green proves nothing: {baseline}"
        )
        assert baseline["commands"], "the baseline names the command it ran"
        assert any(code != 0 for code in baseline["exit_codes"]), (
            f"a red verdict needs a non-zero exit code behind it: {baseline}"
        )


def test_the_baseline_and_the_pass_are_both_readable_from_one_document(
    evidence_client,
) -> None:
    """Both halves, side by side: red before, green after, on the same task."""
    client, plan_id, cycle_id = evidence_client

    body = client.get(f"/api/plans/{plan_id}/cycles/{cycle_id}/evidence").json()
    task = next(
        task
        for goal in body["goals"]
        for task in goal["tasks"]
        if task["test_bundle"] is not None and task["accepted_evidence"]
    )

    assert task["test_bundle"]["baseline"]["verdict"] == "red"
    assert all(item["exit_code"] == 0 for item in task["accepted_evidence"])
    # And the two are distinct commits, which is what makes the pair meaningful.
    assert task["test_bundle"]["test_commit_sha"] != (
        task["accepted_evidence"][0]["candidate_commit_sha"]
    )
