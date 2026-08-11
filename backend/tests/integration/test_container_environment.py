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


def acceptance_containers(binary: str) -> set[str]:
    """Every `praxis-acceptance-*` container currently on the MACHINE."""
    listing = subprocess.run(
        [binary, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {n for n in listing.split() if n.startswith("praxis-acceptance-")}


@pytest.fixture
def no_new_containers(binary: str):
    """Assert THIS test leaked nothing — not that the machine is pristine.

    Phase 10A: both leak tests asserted `"praxis-acceptance-" not in listing`
    against the global list, so a single orphan from any other source failed
    them *permanently* and pointed at teardown, which was not what was wrong.
    Reproduced: an `Exited (137)` container left behind when a test run was
    SIGKILLed mid-flight (the `finally` that removes it cannot run through a
    kill) made `test_no_container_survives_the_run[podman]` fail 2 runs out of
    2 until it was pruned by hand.

    Diffing against a before-snapshot keeps the real assertion — this run
    removed what it created — and drops the accidental one about the machine.
    """
    before = acceptance_containers(binary)
    yield
    survivors = acceptance_containers(binary) - before
    assert not survivors, f"the run leaked {sorted(survivors)}"


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


def test_a_healthcheck_that_never_passes_reports_failed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        healthcheck="test -f /app/never-appears",
        scenario=["true"],
        startup_timeout_seconds=3,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "failed"
    assert "healthy" in verdict.summary


def test_a_passing_healthcheck_lets_the_scenario_run(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        healthcheck="test -f /app/marker.txt",
        scenario=["echo scenario-ran"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed"
    assert "scenario-ran" in verdict.detail


def test_the_run_sees_the_ref_not_the_working_tree(binary: str, repo: Path) -> None:
    """A dirty working tree must not leak into the acceptance run."""
    (repo / "marker.txt").write_text("DIRTY\n", encoding="utf-8")
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["cat /app/marker.txt"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed"
    assert "from-the-repo" in verdict.detail
    assert "DIRTY" not in verdict.detail


def test_a_small_startup_budget_does_not_abort_the_daemon_call(
    binary: str, repo: Path, no_new_containers
) -> None:
    """`startup_timeout_seconds` budgets the APPLICATION, not the daemon.

    Regression: `run -d` was given the operator's app-startup budget, so on a
    loaded machine a legitimate 2s budget timed out the client call while the
    daemon created the container anyway — an `errored` verdict AND a leaked
    container. The verdict here must be the honest `failed` (the healthcheck
    genuinely never passes), never `errored`.
    """
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        healthcheck="test -f /app/never-appears",
        scenario=["true"],
        startup_timeout_seconds=2,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "failed", verdict.summary


def test_no_container_survives_the_run(binary: str, repo: Path, no_new_containers) -> None:
    """Teardown happens on the failure path too."""
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["false"],
        startup_timeout_seconds=60,
    )
    assert env.verify(repo, "HEAD", spec).outcome == "failed"
