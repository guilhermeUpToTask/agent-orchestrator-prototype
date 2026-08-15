"""A project's repository binding is checked when it is written.

Before this, `repo_url` was stored unvalidated and a typo did not fail: the
resolver reported default branch "main" for a path with no .git
(project_workspace.py:66) and the workspace then created, git-init'ed and
committed into it (workspace.py:150-153) — so the plan published green against
an empty repository.
"""

from __future__ import annotations

import subprocess

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.git.project_workspace import ProjectWorkspaceResolver
from praxis_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-B", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
    return path


def test_a_real_repository_is_accepted(client, tmp_path):
    repo = _git_repo(tmp_path / "repo")

    response = client.post("/api/projects", json={"name": "P", "repo_url": str(repo)})

    assert response.status_code == 201


def test_a_path_that_does_not_exist_is_refused(client, tmp_path):
    response = client.post(
        "/api/projects", json={"name": "P", "repo_url": str(tmp_path / "nope")}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"
    assert "does not exist" in response.json()["error"]["message"]


def test_a_directory_that_is_not_a_repository_is_refused(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    response = client.post("/api/projects", json={"name": "P", "repo_url": str(plain)})

    assert response.status_code == 422
    assert "not a git repository" in response.json()["error"]["message"]


def test_a_remote_url_is_accepted_without_touching_the_network(client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("write-time validation must not run git")

    monkeypatch.setattr(subprocess, "run", explode)

    response = client.post(
        "/api/projects",
        json={"name": "P", "repo_url": "https://github.com/example/repo.git"},
    )

    assert response.status_code == 201


def test_a_project_without_a_repo_url_is_still_legal(client):
    response = client.post("/api/projects", json={"name": "P"})

    assert response.status_code == 201


def test_an_update_is_validated_too(client, tmp_path):
    created = client.post("/api/projects", json={"name": "P"}).json()

    response = client.put(
        f"/api/projects/{created['id']}",
        json={"name": "P", "repo_url": str(tmp_path / "nope")},
    )

    assert response.status_code == 422


# ── scp-style remotes ────────────────────────────────────────────────────────
#
# `git@github.com:acme/widgets.git` parses with an EMPTY scheme (`@` and `.` are
# not legal scheme characters), so the most common GitHub remote form was
# treated as a local filesystem path and refused with "repository path
# .../git@github.com:acme/widgets.git does not exist" — a message that names
# the wrong cause and sends the operator looking for a directory.
#
# The form is genuinely unsupported: `repository_path_for` makes the same
# assumption and `_materialize_remote` skips a scheme-less URL, so it was never
# clonable. Refusing it BY NAME is the honest fix.


@pytest.mark.parametrize(
    "repo_url",
    ["git@github.com:acme/widgets.git", "git@gitlab.com:group/sub/proj.git"],
)
def test_an_scp_style_remote_is_refused_by_name(client, repo_url):
    response = client.post("/api/projects", json={"name": "P", "repo_url": repo_url})

    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "scp-style" in message
    assert "ssh://" in message and "https://" in message
    # The old message blamed a filesystem path that was never the point.
    assert "does not exist" not in message


def test_the_ssh_form_of_the_same_remote_is_accepted(client, monkeypatch):
    """The refusal has to name a way forward that actually works."""

    def explode(*args, **kwargs):
        raise AssertionError("write-time validation must not run git")

    monkeypatch.setattr(subprocess, "run", explode)

    response = client.post(
        "/api/projects",
        json={"name": "P", "repo_url": "ssh://git@github.com/acme/widgets.git"},
    )

    assert response.status_code == 201


def test_a_local_path_containing_an_at_sign_is_still_a_path(client, tmp_path):
    """The scp check must not swallow a legitimate directory name."""
    repo = _git_repo(tmp_path / "user@corp" / "repo")

    response = client.post("/api/projects", json={"name": "P", "repo_url": str(repo)})

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# P8.1 — a remote must fail fast, and be checkable before it is bound
# ---------------------------------------------------------------------------


def test_materialize_remote_does_not_block_on_a_credential_prompt(tmp_path):
    """A private https remote makes git ask for a username. There is no tty to
    answer it, so without GIT_TERMINAL_PROMPT=0 the worker blocked forever
    while holding a goal lease. It must fail fast instead."""
    from praxis_orchestrator.domain.entities.project_definition import ProjectDefinition
    from praxis_orchestrator.infra.git.project_workspace import ProjectWorkspaceResolver

    class _NoProjects:
        def get(self, project_id):  # pragma: no cover - never reached
            raise NotImplementedError

        def list(self):
            return []

    resolver = ProjectWorkspaceResolver(_NoProjects(), tmp_path / "home")
    project = ProjectDefinition(
        id="p1",
        name="private",
        # Port 1 on loopback: refused instantly, so the test cannot hang on a
        # TCP connect and still proves the clone terminates rather than prompting.
        repo_url="https://127.0.0.1:1/private/repo.git",
    )

    with pytest.raises(subprocess.CalledProcessError):
        resolver._materialize_remote(project, tmp_path / "clone")


def test_probe_classifies_an_unreachable_host():
    from praxis_orchestrator.infra.git.repository_binding import probe_remote

    probe = probe_remote("https://127.0.0.1:1/a/b.git", timeout_seconds=5.0)

    assert probe.reachable is False
    assert probe.problem_kind in {"unreachable", "timeout", "needs_credentials"}
    assert probe.problem


def test_probe_reads_the_default_branch_of_a_local_repository(tmp_path):
    """A file:// URL is a remote as far as ls-remote is concerned, so the probe
    is exercised end to end with no network."""
    from praxis_orchestrator.infra.git.repository_binding import probe_remote

    origin = tmp_path / "origin"
    _git_repo(origin)

    probe = probe_remote(f"file://{origin}")

    assert probe.reachable is True
    assert probe.default_branch == "main"
    assert probe.problem is None


def test_probe_route_reports_the_resolved_path_preview(client):
    response = client.post(
        "/api/projects/probe", json={"repo_url": "https://127.0.0.1:1/a/b.git"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["binding"] == "remote"
    assert body["reachable"] is False
    assert "/repos/" in body["resolved_path_preview"]



# ---------------------------------------------------------------------------
# P8.1 — an explicitly named binding, and an on-request clone
# ---------------------------------------------------------------------------


def test_naming_remote_with_no_url_is_refused(client):
    """The silent scratch substitution the phase forbids: an operator who says
    'remote' and leaves the URL blank must not quietly get a demo repository."""
    response = client.post(
        "/api/projects", json={"name": "p", "repo_url": None, "binding": "remote"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"
    assert "remote" in response.json()["error"]["message"]


def test_naming_scratch_with_a_url_is_refused(client):
    response = client.post(
        "/api/projects",
        json={"name": "p", "repo_url": "https://example.com/a/b.git", "binding": "scratch"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"


def test_a_binding_that_agrees_is_accepted(client):
    response = client.post(
        "/api/projects",
        json={"name": "p", "repo_url": "https://example.com/a/b.git", "binding": "remote"},
    )

    assert response.status_code == 201


def test_omitting_the_binding_still_works(client):
    """Every fixture and run-cycle.sh posts {name, repo_url}; inference stays."""
    response = client.post("/api/projects", json={"name": "p", "repo_url": None})

    assert response.status_code == 201


def test_clone_is_idempotent_and_reports_the_resolved_path(client, tmp_path):
    repo = _git_repo(tmp_path / "repo")
    created = client.post("/api/projects", json={"name": "p", "repo_url": str(repo)})
    project_id = created.json()["id"]

    first = client.post(f"/api/projects/{project_id}/clone")
    second = client.post(f"/api/projects/{project_id}/clone")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["resolved_path"] == str(repo)
    assert first.json()["resolved_path"] == second.json()["resolved_path"]
    assert first.json()["default_branch"] == "main"
    # A local binding is already materialized: the endpoint reports it rather
    # than copying anything.
    assert first.json()["already_present"] is True


class _NoProjects:
    """A project repository with nothing in it — `_default_branch` and
    `_materialize_remote` are both static/pure with respect to it."""

    def get(self, project_id):  # pragma: no cover - never reached
        raise NotImplementedError

    def list(self):
        return []


def _repo_with_branches(path, *branches: str) -> None:
    """A local repository with no remote — the `binding: "local"` shape."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    for branch in branches:
        subprocess.run(["git", "-C", str(path), "branch", branch], check=True)


def test_the_default_branch_does_not_follow_whichever_branch_is_checked_out(tmp_path):
    """P8.6, found during the 2026-08-10 demo run.

    `_default_branch` probed `symbolic-ref HEAD` as its second fallback, which
    does not answer "what is this repository's default branch" — it answers
    "what is checked out right now". For a LOCAL project there is no
    `origin/HEAD` to answer it first, so checking out any branch silently
    redefined the default one.

    That is not cosmetic. The detected default branch is the ref new cycle
    branches are cut FROM (`workspace.py`), the ref `checkout -B` targets, and
    the ref "plan work never touches the default branch" is measured against.
    The demo's own README tells an operator to `git switch cycle/<id>` to look
    at the result — and doing so made the next verification report the cycle
    branch as the default and fail a guarantee that was actually being kept.
    """
    repo = tmp_path / "repo"
    _repo_with_branches(repo, "cycle/abc123")
    resolver = ProjectWorkspaceResolver(_NoProjects(), tmp_path / "home")

    assert resolver._default_branch(repo) == "main"

    subprocess.run(["git", "-C", str(repo), "switch", "-q", "cycle/abc123"], check=True)

    assert resolver._default_branch(repo) == "main", (
        "checking out a branch redefined the repository's default branch"
    )


def test_a_conventional_default_is_preferred_over_an_alphabetical_accident(tmp_path):
    """`cycle/…` and `goal/…` sort before `main`, and a finished cycle leaves
    plenty of both, so 'first branch' is not a usable rule either."""
    repo = tmp_path / "repo"
    _repo_with_branches(repo, "cycle/abc", "goal/def", "task/ghi")
    resolver = ProjectWorkspaceResolver(_NoProjects(), tmp_path / "home")

    assert resolver._default_branch(repo) == "main"


def test_a_master_repository_still_resolves(tmp_path):
    """Not every repository is `main`, and there is no remote to ask."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "seed"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "branch", "cycle/abc"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "cycle/abc"], check=True)
    resolver = ProjectWorkspaceResolver(_NoProjects(), tmp_path / "home")

    assert resolver._default_branch(repo) == "master"


def test_a_remote_head_still_wins_over_everything(tmp_path):
    """When `origin/HEAD` exists it is authoritative and must stay first: a
    fork whose default is `develop` must not be overridden by a local `main`."""
    origin = tmp_path / "origin"
    _repo_with_branches(origin, "develop")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "symbolic-ref",
         "refs/remotes/origin/HEAD", "refs/remotes/origin/develop"],
        check=True,
    )
    resolver = ProjectWorkspaceResolver(_NoProjects(), tmp_path / "home")

    assert resolver._default_branch(clone) == "develop"
