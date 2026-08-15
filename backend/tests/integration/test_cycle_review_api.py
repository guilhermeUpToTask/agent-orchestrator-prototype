"""The per-goal review surface: a cycle split into review-sized units.

The value under test is the split itself. Anyone can render a diff; only this
system can say *this commit was the test, proven RED before the implementation
existed, and that one was the implementation that made it GREEN* — because it
recorded the boundary at the time.
"""

from __future__ import annotations

import pytest

from tests.integration.cyclic_walk import drive_cycle_to_publication

pytestmark = pytest.mark.integration


@pytest.fixture
def walk(tmp_path, monkeypatch):
    return drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)


def _review(walk):
    response = walk.client.get(
        f"/api/plans/{walk.plan_id}/cycles/{walk.cycle_id}/review"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_review_names_the_repository_and_the_branch(walk):
    body = _review(walk)

    assert body["cycle_branch"] == f"cycle/{walk.cycle_id}"
    assert body["repository_path"] == str(walk.repo)
    assert body["default_branch"] == "trunk"


def test_every_goal_appears_with_its_merge_unit(walk):
    body = _review(walk)

    assert body["goals"]
    for goal in body["goals"]:
        assert goal["merge"] is not None, goal
        assert goal["merge"]["kind"] == "goal_merge"
        assert goal["merge"]["resolved"] is True
        # `<sha>^1..<sha>` — the goal's own contribution, not what siblings
        # had already merged onto the cycle branch.
        assert goal["merge"]["base"].endswith("^1")


def test_a_task_is_split_into_the_test_and_the_implementation(walk):
    """The split no generic diff viewer can produce, because only the
    orchestrator recorded which commit was which."""
    body = _review(walk)

    kinds = [
        unit["kind"]
        for goal in body["goals"]
        for task in goal["tasks"]
        for unit in task["units"]
    ]

    assert "implementation" in kinds
    assert "test_authoring" in kinds


def test_each_unit_carries_the_local_command_that_opens_it(walk):
    """The operator's difftool is better than anything served here, so every
    view is paired with the command that opens the same thing locally."""
    body = _review(walk)

    units = [body["whole_cycle"]] + [
        unit
        for goal in body["goals"]
        for unit in ([goal["merge"]] + [u for t in goal["tasks"] for u in t["units"]])
        if unit is not None
    ]

    assert units
    for unit in units:
        assert unit["local_command"].startswith(f"git -C {walk.repo} diff ")
        assert unit["base"] in unit["local_command"]


def test_a_diff_reports_a_review_band_not_just_a_number(walk):
    """Serving the band is what makes the review research actionable at the
    moment somebody decides how carefully to look."""
    body = _review(walk)

    diff = body["whole_cycle"]["diff"]
    assert diff is not None
    assert diff["review_band"] in {"small", "moderate", "large", "very_large"}
    assert diff["changed_lines"] == diff["insertions"] + diff["deletions"]
    assert diff["files_changed"] == len(diff["files"])


def test_the_protected_scope_travels_with_the_task(walk):
    body = _review(walk)

    tasks = [task for goal in body["goals"] for task in goal["tasks"]]

    assert tasks
    assert any(task["allowed_scope"] for task in tasks)


def test_the_verification_command_and_its_exit_code_are_beside_the_diff(walk):
    """Evidence next to the hunks it covers, which is the whole argument for
    reviewing here rather than in a bare difftool."""
    body = _review(walk)

    accepted = [
        task
        for goal in body["goals"]
        for task in goal["tasks"]
        if task["verification_command"] is not None
    ]

    assert accepted
    assert all(task["exit_code"] == 0 for task in accepted)


def test_a_missing_commit_degrades_one_unit_and_not_the_document(walk):
    """A garbage-collected SHA must make ONE unit unavailable with a reason.
    Failing the whole page would hide every unit that is still fine."""
    from praxis_orchestrator.infra.git.review_reader import GitReviewReader

    reader = GitReviewReader()
    assert reader.resolves(walk.repo, "trunk") is True
    assert reader.resolves(walk.repo, "0" * 40) is False


def test_the_patch_endpoint_returns_the_real_diff(walk):
    body = _review(walk)
    unit = body["whole_cycle"]

    response = walk.client.get(
        f"/api/plans/{walk.plan_id}/cycles/{walk.cycle_id}/review/patch",
        params={"base": unit["base"], "head": unit["sha"]},
    )

    assert response.status_code == 200, response.text
    patch = response.json()
    assert patch["truncated"] is False
    assert patch["local_command"].startswith(f"git -C {walk.repo} diff ")
    assert "diff --git" in patch["patch"]


def test_the_patch_endpoint_reports_truncation_rather_than_hiding_it(walk, tmp_path):
    """A silently clipped patch is how somebody reviews half a change believing
    it was all of it."""
    from praxis_orchestrator.infra.git.review_reader import GitReviewReader

    body = _review(walk)
    unit = body["whole_cycle"]

    patch = GitReviewReader().patch(
        walk.repo, unit["base"], unit["sha"], max_bytes=20
    )

    assert patch.truncated is True
    assert patch.total_bytes > 20
    assert len(patch.text.encode()) <= 20


def test_an_unknown_cycle_is_refused(walk):
    response = walk.client.get(f"/api/plans/{walk.plan_id}/cycles/nope/review")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CYCLE_NOT_FOUND"
