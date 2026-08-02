"""The GitHub adapter, against a scripted transport — no network.

Exercises the real request construction and the real status mapping, so a
change to either fails here rather than at a publication gate.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from agent_orchestrator.app.forge_port import (
    ForgeAuthFailedError,
    ForgeRepoNotFoundError,
    ForgeRequestFailedError,
)
from agent_orchestrator.infra.forge.github import GitHubForge, verify_github_token

pytestmark = pytest.mark.integration


def _transport(handler):
    return httpx.MockTransport(handler)


def _repo_response(push: bool) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "full_name": "acme/widgets",
            "default_branch": "main",
            "permissions": {"push": push},
        },
    )


def test_verify_accepts_a_token_that_can_push():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets"
        assert request.headers["authorization"] == "Bearer ghp_test"
        return _repo_response(push=True)

    identity = verify_github_token(
        "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
    )

    assert identity.repository == "acme/widgets"
    assert identity.default_branch == "main"


def test_verify_refuses_a_token_that_cannot_push():
    """Read access is not enough to open a pull request, and finding that out
    at the publication gate is the failure this check exists to prevent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _repo_response(push=False)

    with pytest.raises(ForgeAuthFailedError) as exc:
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )

    assert "push" in str(exc.value).lower()


def test_verify_maps_404_to_repo_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(ForgeRepoNotFoundError):
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )


def test_verify_maps_401_to_auth_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(ForgeAuthFailedError):
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )


def test_open_pull_request_returns_the_real_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/acme/widgets/pulls"
        return httpx.Response(
            201, json={"html_url": "https://github.com/acme/widgets/pull/7", "number": 7}
        )

    forge = GitHubForge("acme/widgets", SecretStr("ghp_test"), transport=_transport(handler))

    ref = forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")

    assert ref.url == "https://github.com/acme/widgets/pull/7"
    assert ref.number == 7


def test_a_server_error_is_a_forge_request_failure_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    forge = GitHubForge("acme/widgets", SecretStr("ghp_test"), transport=_transport(handler))

    with pytest.raises(ForgeRequestFailedError):
        forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")


def test_the_token_never_appears_in_the_repr():
    """This class is not a dataclass, so nothing else keeps the token out of a
    repr that could reach a log line or a traceback."""
    forge = GitHubForge("acme/widgets", SecretStr("ghp_supersecret"))

    assert "ghp_supersecret" not in repr(forge)


def test_a_failed_push_scrubs_the_token_out_of_git_stderr(tmp_path):
    """git echoes the remote URL on failure, and that URL carries the token."""
    from agent_orchestrator.app.forge_port import ForgePushFailedError

    repo = tmp_path / "repo"
    repo.mkdir()
    forge = GitHubForge("acme/widgets", SecretStr("ghp_supersecret"))

    with pytest.raises(ForgePushFailedError) as exc:
        forge.push_branch(repo, "cycle/abc")

    assert "ghp_supersecret" not in str(exc.value)
