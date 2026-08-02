"""`orchestrate version` — the identity every run report is supposed to carry.

Until this existed the reporting guide told people to run `git rev-parse` by
hand, which an installed copy cannot do at all: there is no repository beside
it. So the two properties that matter are that it never fails, and that it
never reports somebody else's SHA.
"""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

from agent_orchestrator.infra.cli.main import cli


def test_version_prints_the_facts_a_report_needs() -> None:
    result = CliRunner().invoke(cli, ["version"])

    assert result.exit_code == 0, result.output
    assert "agent-orchestrator" in result.output
    for key in ("commit", "python", "platform"):
        assert key in result.output


def test_json_output_is_parseable() -> None:
    result = CliRunner().invoke(cli, ["version", "--json"])

    assert result.exit_code == 0, result.output
    facts = json.loads(result.output)
    assert set(facts) == {"version", "commit", "python", "platform", "executable"}
    assert all(isinstance(value, str) and value for value in facts.values())


def test_the_commit_is_the_orchestrators_not_the_working_directorys(
    tmp_path, monkeypatch
) -> None:
    """An operator runs this from inside the project they are orchestrating.

    Probing `git rev-parse` in the current directory would report THAT
    repository's SHA as the orchestrator's version — a wrong answer that looks
    entirely plausible in a report, and the whole point is comparability.
    """
    other = tmp_path / "someone-elses-repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    subprocess.run(
        ["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@t.test",
         "commit", "-q", "--allow-empty", "-m", "unrelated"],
        check=True,
    )
    foreign_sha = subprocess.run(
        ["git", "-C", str(other), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    monkeypatch.chdir(other)
    facts = json.loads(CliRunner().invoke(cli, ["version", "--json"]).output)

    assert facts["commit"] != foreign_sha


def test_it_survives_having_no_repository_at_all(monkeypatch) -> None:
    """A pipx install has no `.git` anywhere near it. Reporting `unknown` is
    correct; raising, or printing a traceback into someone's report, is not."""
    def _no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    # `_commit` imports subprocess inside the function, so it resolves to this
    # same module object from sys.modules — patching here reaches it.
    monkeypatch.setattr(subprocess, "run", _no_git)

    result = CliRunner().invoke(cli, ["version", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["commit"] == "unknown"
