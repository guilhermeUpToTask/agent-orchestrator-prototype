"""Publication that really opens a pull request.

The ordering under test is the point: the push and the API call are side
effects and must run OUTSIDE any transaction (architectural invariant #5), and
the disposition must be recorded only AFTER the pull request exists — so a
forge failure leaves the gate open with nothing half-written.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.app.forge_port import ForgeRequestFailedError
from agent_orchestrator.app.testing.fakes import FakeForge
from agent_orchestrator.app.use_cases.publish_cycle import publish_cycle
from agent_orchestrator.domain.entities.planning_artifacts import OutputDisposition
from agent_orchestrator.infra.forge.no_forge import NoForge
from tests.integration.cyclic_walk import drive_cycle_to_publication

pytestmark = pytest.mark.integration


@pytest.fixture
def at_the_gate(tmp_path, monkeypatch):
    """A cyclic plan parked at an OPEN completion gate, nothing published."""
    return drive_cycle_to_publication(tmp_path, monkeypatch, publish=False)


def _publish(walk, forge, disposition=OutputDisposition.OPEN_PR, reference=None):
    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    gate = plan.review_gate
    assert gate is not None
    return publish_cycle(
        plan_id=walk.plan_id,
        gate_id=gate.id,
        revision=gate.subject_revision,
        disposition=disposition,
        output_reference=reference,
        uow_factory=walk.container.new_unit_of_work,
        clock=walk.container.clock,
        forge=forge,
        repo_path=walk.repo,
        default_branch="trunk",
    )


def _cycle(walk):
    with walk.container.new_unit_of_work() as uow:
        plan = uow.plans.get(walk.plan_id)
    return next(c for c in plan.cycles if c.id == walk.cycle_id)


def test_open_pr_pushes_then_records_the_real_url(at_the_gate):
    """The recorded reference is now a fact the orchestrator produced, not text
    a human typed — the whole reason forge publication came into P8.1."""
    forge = FakeForge()

    reference = _publish(at_the_gate, forge)

    assert forge.pushes == [(at_the_gate.repo, f"cycle/{at_the_gate.cycle_id}")]
    assert reference == "https://github.test/o/r/pull/1"
    cycle = _cycle(at_the_gate)
    assert cycle.output_reference == "https://github.test/o/r/pull/1"
    assert cycle.output_disposition == OutputDisposition.OPEN_PR


def test_the_pull_request_targets_the_default_branch_from_the_cycle_branch(at_the_gate):
    forge = FakeForge()

    _publish(at_the_gate, forge)

    assert forge.pull_requests[0]["head"] == f"cycle/{at_the_gate.cycle_id}"
    assert forge.pull_requests[0]["base"] == "trunk"


def test_the_body_carries_the_evidence(at_the_gate):
    forge = FakeForge()

    _publish(at_the_gate, forge)

    body = forge.pull_requests[0]["body"]
    assert "Verification evidence" in body
    assert "does not merge pull requests" in body


def test_the_push_happens_before_the_pull_request(at_the_gate):
    forge = FakeForge(fail_on="pull_request")

    with pytest.raises(ForgeRequestFailedError):
        _publish(at_the_gate, forge)

    assert forge.pushes  # the push did happen
    assert forge.pull_requests == []


def test_a_forge_failure_leaves_the_gate_open_and_records_nothing(at_the_gate):
    """The invariant that makes this safe to retry: the disposition is written
    only after the pull request exists."""
    forge = FakeForge(fail_on="push")

    with pytest.raises(ForgeRequestFailedError):
        _publish(at_the_gate, forge)

    cycle = _cycle(at_the_gate)
    assert cycle.output_disposition is None
    assert cycle.output_reference is None
    with at_the_gate.container.new_unit_of_work() as uow:
        plan = uow.plans.get(at_the_gate.plan_id)
    assert plan.review_gate is not None  # still open, still actionable


def test_a_failed_publication_can_be_retried_once_the_forge_works(at_the_gate):
    with pytest.raises(ForgeRequestFailedError):
        _publish(at_the_gate, FakeForge(fail_on="push"))

    reference = _publish(at_the_gate, FakeForge())

    assert reference == "https://github.test/o/r/pull/1"
    assert _cycle(at_the_gate).output_disposition == OutputDisposition.OPEN_PR


def test_retain_branch_never_touches_the_forge(at_the_gate):
    forge = FakeForge(fail_on="push")  # would raise if it were consulted

    _publish(
        at_the_gate,
        forge,
        disposition=OutputDisposition.RETAIN_BRANCH,
        reference=f"cycle/{at_the_gate.cycle_id}",
    )

    assert forge.pushes == []
    assert _cycle(at_the_gate).output_disposition == OutputDisposition.RETAIN_BRANCH


def test_open_pr_with_no_forge_still_records_a_typed_reference(at_the_gate):
    """Existing behaviour is not removed: an installation with no token keeps
    recording the disposition the operator typed. NoForge is a supported
    configuration, not a broken one."""
    reference = _publish(
        at_the_gate, NoForge(), reference="https://github.com/me/mine/pull/4"
    )

    assert reference == "https://github.com/me/mine/pull/4"
    assert _cycle(at_the_gate).output_reference == "https://github.com/me/mine/pull/4"
