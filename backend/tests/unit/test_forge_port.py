"""The Forge port, its permanent fallback, and its fake.

The scope limits are structural rather than documented: there is no merge
method to call and no way to name a branch other than the one passed in, so a
future caller cannot reach for either by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis_orchestrator.app.forge_port import (
    ForgeNotConfiguredError,
    ForgePort,
    ForgeRequestFailedError,
    PullRequestRef,
)
from praxis_orchestrator.app.testing.fakes import FakeForge
from praxis_orchestrator.infra.forge.no_forge import NoForge


def test_no_forge_refuses_both_operations_with_an_actionable_message():
    """NoForge is the permanent fallback, not a placeholder: an installation
    with no GitHub token is a supported configuration, and it says what to do
    rather than degrading silently."""
    forge = NoForge()

    with pytest.raises(ForgeNotConfiguredError) as push_error:
        forge.push_branch(Path("/tmp/repo"), "cycle/abc")
    with pytest.raises(ForgeNotConfiguredError) as pr_error:
        forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")

    assert "Settings" in str(push_error.value)
    assert "Settings" in str(pr_error.value)


def test_both_adapters_satisfy_the_protocol():
    assert isinstance(NoForge(), ForgePort)
    assert isinstance(FakeForge(), ForgePort)


def test_the_port_has_no_merge_counterpart():
    """The orchestrator opens a pull request and never merges one. Locked here
    because the guarantee is only as strong as the absence of the method."""
    assert not hasattr(NoForge(), "merge_pull_request")
    assert not hasattr(FakeForge(), "merge_pull_request")


def test_fake_forge_records_what_it_was_asked_to_do():
    forge = FakeForge()

    forge.push_branch(Path("/tmp/repo"), "cycle/abc")
    ref = forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")

    assert forge.pushes == [(Path("/tmp/repo"), "cycle/abc")]
    assert forge.pull_requests == [
        {"head": "cycle/abc", "base": "main", "title": "t", "body": "b"}
    ]
    assert isinstance(ref, PullRequestRef)
    assert ref.number == 1


def test_fake_forge_can_be_scripted_to_fail_at_either_step():
    with pytest.raises(ForgeRequestFailedError):
        FakeForge(fail_on="push").push_branch(Path("/tmp/repo"), "cycle/abc")

    pushed = FakeForge(fail_on="pull_request")
    pushed.push_branch(Path("/tmp/repo"), "cycle/abc")
    with pytest.raises(ForgeRequestFailedError):
        pushed.open_pull_request(head="cycle/abc", base="main", title="t", body="b")
