"""
tests/unit/infra/test_dependency_checker.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.dependency_checker import (
    DepResult,
    DependencyChecker,
    DependencyReport,
    _check_binary,
    _check_git,
    _check_redis,
)


# ── DepResult / DependencyReport ──────────────────────────────────────────────


def test_report_all_ok_when_all_results_ok():
    report = DependencyReport(
        results=[
            DepResult("a", ok=True, message=""),
            DepResult("b", ok=True, message=""),
        ]
    )
    assert report.all_ok is True


def test_report_not_all_ok_when_one_fails():
    report = DependencyReport(
        results=[
            DepResult("a", ok=True, message=""),
            DepResult("b", ok=False, message=""),
        ]
    )
    assert report.all_ok is False


def test_can_start_requires_redis_git_and_one_runtime():
    report = DependencyReport(
        results=[
            DepResult("redis", ok=True, message=""),
            DepResult("git", ok=True, message=""),
            DepResult("gemini-cli", ok=True, message="", is_runtime=True),
        ]
    )
    assert report.can_start is True


def test_can_start_false_when_redis_missing():
    report = DependencyReport(
        results=[
            DepResult("redis", ok=False, message=""),
            DepResult("git", ok=True, message=""),
            DepResult("gemini-cli", ok=True, message="", is_runtime=True),
        ]
    )
    assert report.can_start is False


def test_can_start_false_when_no_runtime():
    report = DependencyReport(
        results=[
            DepResult("redis", ok=True, message=""),
            DepResult("git", ok=True, message=""),
            DepResult("gemini-cli", ok=False, message="", is_runtime=True),
            DepResult("claude-code", ok=False, message="", is_runtime=True),
        ]
    )
    assert report.can_start is False


def test_failing_returns_only_bad_results():
    ok = DepResult("redis", ok=True, message="")
    bad = DepResult("git", ok=False, message="nope")
    report = DependencyReport(results=[ok, bad])
    assert report.failing() == [bad]


# ── _check_redis ──────────────────────────────────────────────────────────────


def test_check_redis_ok_when_ping_succeeds():
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    with patch("src.infra.dependency_checker.redis.from_url", return_value=mock_client):
        result = _check_redis("redis://localhost:6379/0")
    assert result.ok is True
    assert result.name == "redis"


def test_check_redis_fails_when_connection_refused():
    mock_client = MagicMock()
    mock_client.ping.side_effect = ConnectionRefusedError("refused")
    with patch("src.infra.dependency_checker.redis.from_url", return_value=mock_client):
        result = _check_redis("redis://localhost:6379/0")
    assert result.ok is False
    assert result.install_hint != ""


def test_check_redis_fails_gracefully_on_bad_url():
    with patch("src.infra.dependency_checker.redis.from_url", side_effect=Exception("bad url")):
        result = _check_redis("redis://nonexistent:9999/0")
    assert result.ok is False


# ── _check_git ────────────────────────────────────────────────────────────────


def test_check_git_ok_when_binary_present():
    with patch("src.infra.dependency_checker.shutil.which", return_value="/usr/bin/git"):
        with patch("src.infra.dependency_checker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="git version 2.40.0", stderr="")
            result = _check_git()
    assert result.ok is True
    assert "git" in result.message


def test_check_git_fails_when_binary_missing():
    with patch("src.infra.dependency_checker.shutil.which", return_value=None):
        result = _check_git()
    assert result.ok is False
    assert result.install_hint != ""


# ── _check_binary ─────────────────────────────────────────────────────────────


def test_check_binary_ok_when_found():
    with patch("src.infra.dependency_checker.shutil.which", return_value="/usr/local/bin/gemini"):
        with patch("src.infra.dependency_checker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="gemini 1.2.3", stderr="")
            result = _check_binary("gemini-cli", "gemini", "npm install -g @google/gemini-cli")
    assert result.ok is True
    assert result.is_runtime is True


def test_check_binary_fails_when_not_found():
    with patch("src.infra.dependency_checker.shutil.which", return_value=None):
        result = _check_binary("gemini-cli", "gemini", "npm install -g @google/gemini-cli")
    assert result.ok is False
    assert result.install_hint == "npm install -g @google/gemini-cli"
    assert result.is_runtime is True


# ── DependencyChecker integration ─────────────────────────────────────────────


def test_checker_run_returns_report():
    def fake_redis(url):
        return DepResult("redis", ok=True, message="ok")

    def fake_git():
        return DepResult("git", ok=True, message="git version 2.40")

    # Patch all individual check functions
    with (
        patch(
            "src.infra.dependency_checker._check_redis",
            return_value=DepResult("redis", ok=True, message=""),
        ),
        patch(
            "src.infra.dependency_checker._check_git", return_value=DepResult("git", ok=True, message="")
        ),
        patch(
            "src.infra.dependency_checker._check_binary",
            return_value=DepResult("gemini-cli", ok=True, message="", is_runtime=True),
        ),
    ):
        checker = DependencyChecker(redis_url="redis://localhost:6379/0")
        report = checker.run()

    assert isinstance(report, DependencyReport)
    assert len(report.results) > 0


def test_checker_extra_checks_are_included():
    extra = DepResult("custom", ok=True, message="custom ok")

    with (
        patch(
            "src.infra.dependency_checker._check_redis",
            return_value=DepResult("redis", ok=True, message=""),
        ),
        patch(
            "src.infra.dependency_checker._check_git", return_value=DepResult("git", ok=True, message="")
        ),
        patch(
            "src.infra.dependency_checker._check_binary",
            return_value=DepResult("r", ok=True, message="", is_runtime=True),
        ),
    ):
        checker = DependencyChecker(extra_checks=[lambda: extra])
        report = checker.run()

    assert any(r.name == "custom" for r in report.results)
