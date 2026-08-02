"""The ProjectEnvironment port and its permanent fallback.

The property that matters most is a negative one: an acceptance run OBSERVES.
It must never be able to fail a promotion that passed verification, or stop a
publication gate from opening.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.app.environment_port import (
    AcceptanceVerdict,
    EnvironmentSpec,
    ProjectEnvironment,
)
from agent_orchestrator.infra.environment.no_environment import NoEnvironment


def test_no_environment_satisfies_the_protocol():
    assert isinstance(NoEnvironment(), ProjectEnvironment)


def test_no_environment_reports_skipped_not_passed():
    """`skipped` is a first-class outcome. An unconfigured project must not
    show a reassuring green that nothing actually earned."""
    verdict = NoEnvironment().verify(Path("/tmp/repo"), "cycle/abc", None)

    assert verdict.outcome == "skipped"
    assert verdict.is_signal is False


def test_no_environment_explains_what_was_and_was_not_proved():
    verdict = NoEnvironment().verify(Path("/tmp/repo"), "cycle/abc", None)

    assert "did not prove the application runs" in verdict.detail


def test_no_environment_never_raises_even_on_a_nonexistent_path():
    """`verify` is contracted not to raise. The fallback is the one adapter
    that must never break that, because it runs on every default install."""
    verdict = NoEnvironment().verify(
        Path("/definitely/not/here"), "cycle/abc", EnvironmentSpec(image="x")
    )

    assert verdict.outcome == "skipped"


def test_a_passed_verdict_is_a_signal_and_a_skipped_one_is_not():
    assert AcceptanceVerdict(outcome="passed", summary="ok").is_signal
    assert AcceptanceVerdict(outcome="failed", summary="no").is_signal
    assert AcceptanceVerdict(outcome="errored", summary="boom").is_signal
    assert not AcceptanceVerdict(outcome="skipped", summary="nobody asked").is_signal


def test_the_port_cannot_express_a_gate():
    """The verdict is advisory by construction: the port returns a value and
    has no way to refuse, cancel, or block anything."""
    assert not hasattr(NoEnvironment(), "block_publication")
    assert not hasattr(NoEnvironment(), "require")
