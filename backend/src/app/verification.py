"""Portable deterministic checks for frozen tests and task scope."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.domain.entities.execution_contracts import (
    TaskContract,
    TestBundle,
    VerificationStrategy,
)

_BYPASS_MARKERS = (
    "pytest.skip(",
    "@pytest.mark.skip",
    "@pytest.mark.xfail",
    "unittest.skip",
    "test.skip(",
    "test.only(",
    ".skip(",
)


@dataclass(frozen=True)
class CandidateValidation:
    accepted: bool
    reasons: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_scope(path: str, scope: str) -> bool:
    """Match repository-relative paths without treating `.` as a literal prefix."""
    scope_value = scope.replace("\\", "/")
    if scope_value in {"", ".", "./"}:
        return True
    normalized_path = path.replace("\\", "/")
    while normalized_path.startswith("./"):
        normalized_path = normalized_path[2:]
    while scope_value.startswith("./"):
        scope_value = scope_value[2:]
    normalized_path = normalized_path.strip("/")
    normalized_scope = scope_value.strip("/")
    return normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/")


def is_check_path(path: str) -> bool:
    """Does this path hold executable checks rather than production code?"""
    normalized = path.replace("\\", "/")
    name = normalized.rstrip("/").rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or name.startswith("test_")
        or name in {"conftest.py", "pytest.ini", "tests"}
    )


def test_author_path_allowed(path: str, strategy: VerificationStrategy) -> bool:
    """May the TEST-AUTHORING stage write this path?

    The RED stage authors executable checks and must never touch production
    files. Shared with the reasoner's submission-time check so a frozen contract
    cannot declare a strategy whose first stage it makes impossible: a `tdd`
    contract whose `allowed_scope` names only production files leaves the test
    author with nowhere legal to write, and NO agent can satisfy it.
    """
    if strategy == VerificationStrategy.EXECUTABLE_CHECK:
        return True
    return is_check_path(path)


# Deliberately NOT here: a `discover_executable_checks(root)` that scans the
# repository for test files. It was written, and it is the wrong idea. A scan
# cannot tell task 3's checks from task 1's, so on a multi-task goal it happily
# freezes another task's failing test as this task's evidence. WHICH tests prove
# a task is done is intent, not a property of the tree, so it has to be declared
# — see `src/app/test_identity.py`.


def validate_candidate(
    root: Path,
    contract: TaskContract,
    bundle: TestBundle,
    changed_paths: Iterable[str],
) -> CandidateValidation:
    reasons: list[str] = []
    if not bundle.validates(contract.id, contract.revision):
        reasons.append("test bundle does not match task revision")

    changed = {Path(path).as_posix() for path in changed_paths}
    for protected, expected_hash in bundle.protected_file_hashes.items():
        path = root / protected
        if not path.is_file():
            reasons.append(f"protected test missing or renamed: {protected}")
            continue
        if sha256_file(path) != expected_hash:
            reasons.append(f"protected test changed: {protected}")
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in _BYPASS_MARKERS):
            reasons.append(f"test bypass marker present: {protected}")

    allowed = tuple(contract.allowed_scope)
    forbidden = tuple(contract.forbidden_scope)
    protected_paths = set(bundle.protected_file_hashes)
    config_names = {
        "pytest.ini",
        "pyproject.toml",
        "tox.ini",
        "package.json",
        "vitest.config.ts",
        "jest.config.js",
    }
    for changed_path in sorted(changed):
        if changed_path in protected_paths:
            continue
        if Path(changed_path).name in config_names:
            reasons.append(f"verification configuration changed: {changed_path}")
        if forbidden and any(_matches_scope(changed_path, prefix) for prefix in forbidden):
            reasons.append(f"forbidden path changed: {changed_path}")
        if allowed and not any(_matches_scope(changed_path, prefix) for prefix in allowed):
            reasons.append(f"path outside allowed scope: {changed_path}")

    return CandidateValidation(not reasons, tuple(dict.fromkeys(reasons)))
