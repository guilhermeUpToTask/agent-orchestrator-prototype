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
