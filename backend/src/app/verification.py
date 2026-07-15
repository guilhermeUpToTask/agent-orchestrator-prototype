"""Portable deterministic checks for frozen tests and task scope."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.domain.entities.execution_contracts import TaskContract, TestBundle

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
        if forbidden and any(changed_path.startswith(prefix) for prefix in forbidden):
            reasons.append(f"forbidden path changed: {changed_path}")
        if allowed and not any(changed_path.startswith(prefix) for prefix in allowed):
            reasons.append(f"path outside allowed scope: {changed_path}")

    return CandidateValidation(not reasons, tuple(dict.fromkeys(reasons)))
