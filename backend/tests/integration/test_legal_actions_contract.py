"""Phase 4's exit criterion: every advertised action works in the state that
advertises it.

`Plan.legal_actions` publishes raw strings — `pause`, `retry_stage`,
`review:approve` — and `block_policy.py` mapped them to routes in a COMMENT,
which no client can read and no test can execute. An action the plan itself
advertised that then 404s or 422s is the worst failure this API has: the
operator's only visible next step does not exist.

Both properties below are driven by SERVED data — the plan document's own
`action_endpoints` and the app's own OpenAPI inventory — so there is no second
copy of the mapping in this file to drift from the one in `plans.py`.

This is the same shape as `tests/unit/orchestration/test_block_policy.py`, which
already asserts that every resolution a BLOCK advertises is accepted by its
route; this extends it from block resolutions to the whole vocabulary.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agent_orchestrator.api import dependencies
from agent_orchestrator.api.server import create_app
from agent_orchestrator.domain.entities.project_definition import ProjectDefinition
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

# The failures that mean "this action was never really available". A 409 is
# legitimate — a worker holding the claim is a race, not a broken contract —
# and a 500 would be a different bug, caught by its own test.
NEVER_ACCEPTABLE = {404, 405, 422}


def _served_operations() -> set[str]:
    paths = create_app().openapi()["paths"]
    return {
        f"{method.upper()} {path}"
        for path, operations in paths.items()
        for method in operations
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Test project", repo_url=None)
    )
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def _new_plan(client) -> str:
    created = client.post(
        "/api/plans", json={"brief": "goal: G\ntask: t", "project_id": "project-1"}
    )
    assert created.status_code == 201, created.text
    return created.json()["plan_id"]


def _at_intent_gate(client) -> str:
    plan_id = _new_plan(client)
    client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": "Deliver it"})
    return plan_id


def _at_cycle_draft_gate(client) -> str:
    plan_id = _at_intent_gate(client)
    gate = client.get(f"/api/plans/{plan_id}").json()["pending_gate"]
    client.post(
        f"/api/plans/{plan_id}/intent/approve",
        json={"gate_id": gate["id"], "subject_revision": gate["subject_revision"]},
    )
    return plan_id


def _paused(client) -> str:
    plan_id = _at_cycle_draft_gate(client)
    client.post(f"/api/plans/{plan_id}/pause", json={"reason": "operator inspection"})
    return plan_id


_STATES = {
    "fresh": _new_plan,
    "intent_gate": _at_intent_gate,
    "cycle_draft_gate": _at_cycle_draft_gate,
    "paused": _paused,
}


@pytest.mark.parametrize("state", sorted(_STATES))
def test_every_advertised_action_names_a_real_operation(client, state):
    plan_id = _STATES[state](client)
    plan = client.get(f"/api/plans/{plan_id}").json()

    missing = set(plan["legal_actions"]) - set(plan["action_endpoints"])
    assert not missing, (
        f"state {state!r} advertises {sorted(missing)} with no endpoint. "
        "legal_actions publishes raw strings, so an action missing from "
        "action_endpoints leaves the client nothing to call"
    )

    served = _served_operations()
    for action, route in plan["action_endpoints"].items():
        assert route in served, (
            f"state {state!r} maps {action!r} to {route!r}, which the app does not serve"
        )


@pytest.mark.parametrize("state", sorted(_STATES))
def test_every_advertised_action_is_accepted_in_that_state(client, state):
    """The criterion itself. Each action is called through its own served
    endpoint with a minimal body; what matters is that the route exists and the
    plan is in a state that accepts it, not that the call does useful work."""
    plan_id = _STATES[state](client)
    plan = client.get(f"/api/plans/{plan_id}").json()

    for action, route in plan["action_endpoints"].items():
        method, template = route.split(" ", 1)
        url = template.replace("{plan_id}", plan_id)
        body = _minimal_body(action, plan)

        response = client.request(method, url, json=body)

        assert response.status_code not in NEVER_ACCEPTABLE, (
            f"state {state!r} advertised {action!r}, but {route} answered "
            f"{response.status_code}: {response.text[:300]}"
        )
        # Re-read: an accepted action may legally change the state, and the
        # next action in this loop must be judged against the CURRENT state,
        # not the one captured before the loop began.
        plan = client.get(f"/api/plans/{plan_id}").json()
        if action not in plan["action_endpoints"]:
            break


def _minimal_body(action: str, plan: dict) -> dict:
    """The smallest body each endpoint will accept. A 422 from a missing field
    would be indistinguishable from a 422 meaning "not legal here", which is the
    thing this test is trying to detect."""
    if action.startswith("review:"):
        gate = plan["pending_gate"]
        body = {"gate_id": gate["id"], "subject_revision": gate["subject_revision"]}
        if gate["subject_type"] == "cycle_completion":
            body |= {"disposition": "discard", "output_reference": None}
        return body
    if action == "start_intent":
        return {"objective": "contract probe", "scope": [], "constraints": [], "exclusions": []}
    if action in {"edit_pending_work", "edit_task"}:
        goal = next(iter(plan.get("goals") or []), None)
        return {"type": "update_goal", "goal_id": goal["id"] if goal else "unknown"}
    if action == "bind_project":
        return {"project_id": plan["project_id"] or "project-1"}
    if action == "pause":
        return {"reason": "contract probe"}
    return {}
