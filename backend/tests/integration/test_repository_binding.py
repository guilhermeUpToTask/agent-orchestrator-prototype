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

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
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
