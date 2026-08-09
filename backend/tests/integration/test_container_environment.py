"""ContainerEnvironment against REAL containers.

See P8.5: a scripted fake CLI loses exactly the behaviour this adapter exists to
prove. The devcontainer this guest replaced could start a container but not
isolate one, and a test that only asserts "the command was constructed" would
have passed there. These run against whatever runtimes are actually on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.app.environment_port import EnvironmentSpec
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.container]

BINARIES = [b for b in ("docker", "podman") if shutil.which(b)]
IMAGE = "docker.io/library/alpine:3.20"


@pytest.fixture(params=BINARIES or ["docker"])
def binary(request: pytest.FixtureRequest) -> str:
    if not BINARIES:
        pytest.skip("no container runtime on PATH")
    return str(request.param)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit, so `ref` means something."""
    path = tmp_path / "project"
    path.mkdir()

    def run(*a: str) -> None:
        subprocess.run(a, cwd=path, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (path / "marker.txt").write_text("from-the-repo\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return path


def test_a_passing_scenario_reports_passed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["cat /app/marker.txt"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed", verdict.detail
    assert "from-the-repo" in verdict.detail


def test_a_failing_scenario_reports_failed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["test -f /app/does-not-exist"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "failed"


def test_no_spec_is_skipped_not_passed(binary: str, repo: Path) -> None:
    verdict = ContainerEnvironment(binary=binary).verify(repo, "HEAD", None)
    assert verdict.outcome == "skipped"
    assert verdict.is_signal is False
