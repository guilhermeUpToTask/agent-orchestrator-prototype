"""The two paths a live daemon cannot be made to take on demand: the binary is
absent, and the daemon refuses.

Failure injection, NOT a substitute for the real-container tests in
test_container_environment.py — every path that can be exercised for real is
exercised for real there. These exist because you cannot uninstall docker
mid-suite, and "the binary exists but containers do not work here" is a real
state on other people's machines that must return `errored` rather than hang.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.app.environment_port import EnvironmentSpec
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment

pytestmark = pytest.mark.integration

SPEC = EnvironmentSpec(image="alpine:3.20", command="sleep 1", scenario=["true"])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A REAL git repo.

    Load-bearing, not scenery: without it `git worktree add` fails first and
    every test below passes on that error instead of the one it names. The
    first draft of this file did exactly that — the daemon-refusal case never
    reached the daemon and would have passed with the refusal path deleted.
    """
    path = tmp_path / "project"
    path.mkdir()

    def run(*a: str) -> None:
        subprocess.run(a, cwd=path, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (path / "marker.txt").write_text("seed\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return path


def _scripted(tmp_path: Path, body: str) -> str:
    script = tmp_path / "faked-cli"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_a_missing_binary_errors_with_an_actionable_message(repo: Path) -> None:
    verdict = ContainerEnvironment(binary="definitely-not-installed").verify(
        repo, "HEAD", SPEC
    )
    assert verdict.outcome == "errored"
    assert "not installed" in verdict.summary
    assert "environment.container_binary" in verdict.detail


def test_a_daemon_that_refuses_errors_rather_than_hanging(
    tmp_path: Path, repo: Path
) -> None:
    cli = _scripted(tmp_path, 'echo "Cannot connect to the daemon" >&2; exit 1')
    verdict = ContainerEnvironment(binary=cli).verify(repo, "HEAD", SPEC)
    assert verdict.outcome == "errored"
    # The operator has to be able to tell a refusing daemon from every other
    # way a run can error, so the daemon's own words must survive into detail.
    assert "The container did not start." in verdict.summary
    assert "Cannot connect to the daemon" in verdict.detail


def test_verify_never_raises_even_on_a_nonsense_repo(tmp_path: Path) -> None:
    verdict = ContainerEnvironment(binary="echo").verify(
        tmp_path / "no-such-repo", "HEAD", SPEC
    )
    assert verdict.outcome == "errored"
