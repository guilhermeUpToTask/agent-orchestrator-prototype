"""GitHub adapter for the Forge port.

GitHub only, on purpose: guessing GitLab or Gitea semantics with no user asking
is the completeness the roadmap's scope discipline forbids. The port makes a
second adapter cheap when someone does ask.

The token is held as a SecretStr and crosses into plaintext only inside the
Authorization header of one request, or the remote URL of one push. It never
reaches a log line, a repr, or an exception message — the push scrubs git's
stderr, which echoes the remote URL on failure.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog
from pydantic import SecretStr

from praxis_orchestrator.app.forge_port import (
    ForgeAuthFailedError,
    ForgePushFailedError,
    ForgeRepoNotFoundError,
    ForgeRequestFailedError,
    PullRequestRef,
)
from praxis_orchestrator.infra.git.repository_binding import GIT_NONINTERACTIVE_ENV

log = structlog.get_logger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 15.0


@dataclass(frozen=True)
class GitHubIdentity:
    repository: str
    default_branch: str


def _headers(token: SecretStr) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _raise_for_status(response: httpx.Response, repository: str) -> None:
    if response.status_code in (401, 403):
        raise ForgeAuthFailedError(
            f"GitHub rejected the token for {repository} ({response.status_code}). "
            "Check that it is valid and has repository write access."
        )
    if response.status_code == 404:
        raise ForgeRepoNotFoundError(
            f"GitHub has no repository {repository} reachable with this token. "
            "A private repository the token cannot see also reports 404."
        )
    if response.status_code >= 400:
        raise ForgeRequestFailedError(
            f"GitHub returned {response.status_code} for {repository}"
        )


def verify_github_token(
    repository: str,
    token: SecretStr,
    *,
    transport: httpx.BaseTransport | None = None,
) -> GitHubIdentity:
    """One call that answers three questions at once: does the repository
    exist, does the token reach it, and can it push.

    Called at save time so a bad credential fails at setup rather than at the
    publication gate, twenty-five minutes into a cycle.
    """
    try:
        with httpx.Client(base_url=_API, timeout=_TIMEOUT, transport=transport) as client:
            response = client.get(f"/repos/{repository}", headers=_headers(token))
    except httpx.HTTPError as exc:
        raise ForgeRequestFailedError(f"could not reach GitHub: {exc}") from exc

    _raise_for_status(response, repository)
    payload = response.json()
    if not payload.get("permissions", {}).get("push", False):
        raise ForgeAuthFailedError(
            f"the token reaches {repository} but cannot push to it; "
            "opening a pull request needs write access"
        )
    return GitHubIdentity(
        repository=payload["full_name"], default_branch=payload["default_branch"]
    )


class GitHubForge:
    def __init__(
        self,
        repository: str,
        token: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._token = token
        self._transport = transport

    def __repr__(self) -> str:
        # Explicit: this is not a dataclass, so nothing else guarantees the
        # token stays out of a repr that could reach a log or a traceback.
        return f"GitHubForge(repository={self._repository!r})"

    def push_branch(self, repo: Path, branch: str) -> None:
        secret = self._token.get_secret_value()
        url = f"https://x-access-token:{secret}@github.com/{self._repository}.git"
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "push",
                url,
                f"refs/heads/{branch}:refs/heads/{branch}",
            ],
            capture_output=True,
            text=True,
            # Deliberately NOT inheriting os.environ: a push carrying its own
            # credential in the URL must not also pick up an ambient credential
            # helper that could redirect or override it.
            env={**GIT_NONINTERACTIVE_ENV},
        )
        if result.returncode != 0:
            # git echoes the remote URL on failure, and that URL carries the token.
            scrubbed = result.stderr.replace(secret, "***").strip()
            log.warning("forge.push_failed", repository=self._repository, branch=branch)
            raise ForgePushFailedError(
                f"pushing {branch} to {self._repository} was refused: {scrubbed}"
            )
        log.info("forge.pushed", repository=self._repository, branch=branch)

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        try:
            with httpx.Client(
                base_url=_API, timeout=_TIMEOUT, transport=self._transport
            ) as client:
                response = client.post(
                    f"/repos/{self._repository}/pulls",
                    headers=_headers(self._token),
                    json={"head": head, "base": base, "title": title, "body": body},
                )
        except httpx.HTTPError as exc:
            raise ForgeRequestFailedError(f"could not reach GitHub: {exc}") from exc

        _raise_for_status(response, self._repository)
        payload = response.json()
        log.info(
            "forge.pull_request_opened",
            repository=self._repository,
            number=payload["number"],
        )
        return PullRequestRef(url=payload["html_url"], number=payload["number"])
