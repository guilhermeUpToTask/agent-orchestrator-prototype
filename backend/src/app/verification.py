"""Portable deterministic checks for frozen tests and task scope."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Set

from src.domain.entities.execution_contracts import (
    TaskContract,
    TestBundle,
    VerificationStrategy,
)

_CONFIG_NAMES = frozenset(
    {
        "pytest.ini",
        "pyproject.toml",
        "tox.ini",
        "package.json",
        "vitest.config.ts",
        "jest.config.js",
    }
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
class BaselineOutcome:
    """What the checks did BEFORE the work, and whether that is acceptable."""

    verdict: str  # 'red' | 'green' — the observed fact
    accepted: bool  # whether this strategy wanted that
    expectation: str  # phrase for the rejection message


def baseline_outcome(
    strategy: VerificationStrategy, exit_codes: Sequence[int]
) -> BaselineOutcome:
    """One definition, used by the accept/reject branch AND by the recorded fact.

    A check that already passes proves nothing about THIS task: the green after
    the implementation would be the same green as before it. So the baseline must
    FAIL — for a check the author just wrote (`tdd`) and equally for one the
    contract named (`executable_check`, the bug-fix workflow). They differ only in
    who typed the test.

    `characterization` is the single exception, and the reason this returns a
    verdict alongside the boolean: it pins behaviour that ALREADY works, so it
    must pass. It is being retired — no coverage until recently, never run live,
    and its prompt told the agent to write failing tests — but is honoured while
    it exists.
    """
    verdict = "red" if any(code != 0 for code in exit_codes) else "green"
    if strategy == VerificationStrategy.CHARACTERIZATION:
        return BaselineOutcome(
            verdict=verdict,
            accepted=bool(exit_codes) and verdict == "green",
            expectation="a passing characterization baseline",
        )
    return BaselineOutcome(
        verdict=verdict,
        accepted=bool(exit_codes) and verdict == "red",
        expectation="a meaningful RED result",
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


#: Directories regenerated per worktree or vendored. Pruned during a walk rather
#: than filtered after it — `rglob` would descend into `node_modules` first.
_BYPRODUCT_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".git",
        "dist",
        "build",
        "htmlcov",
    }
)


def is_byproduct_path(path: str) -> bool:
    """Regenerated per worktree, never evidence.

    A `.pyc` embeds the source mtime, and `tests/__pycache__/test_x.cpython-311.pyc`
    matches `is_check_path` on its basename — so hashing it as a protected check
    makes every later candidate fail a hash it can never reproduce.
    """
    parts = path.replace("\\", "/").split("/")
    return (
        bool(_BYPRODUCT_DIRS.intersection(parts))
        or path.endswith((".pyc", ".pyo"))
        or any(part.endswith(".egg-info") for part in parts)
        or parts[-1].startswith(".coverage")
    )


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
    files — for EVERY strategy. This used to return True unconditionally for
    `executable_check`, on the assumption that the strategy has no authoring
    stage at all. It does: when a contract names no check that already exists,
    the author still runs, and the blanket allow let it write production code
    which then got hashed into `protected_file_hashes` as though it were a check
    — making the implementer's scope guard skip it entirely.

    `strategy` is retained in the signature because the reasoner's
    submission-time validation and `contract_repair` both call it per strategy,
    and because a future strategy may legitimately narrow it further.
    """
    del strategy  # every stage answers the same today; see the docstring
    return is_check_path(path)


# Deliberately NOT here: a `discover_executable_checks(root)` that scans the
# repository for test files. It was written, and it is the wrong idea. A scan
# cannot tell task 3's checks from task 1's, so on a multi-task goal it happily
# freezes another task's failing test as this task's evidence. WHICH tests prove
# a task is done is intent, not a property of the tree, so it has to be declared
# — see `src/app/test_identity.py`.


def check_protected(root: Path, protected: Mapping[str, str]) -> list[str]:
    """Are the pinned checks still exactly what was frozen?

    The bypass-marker scan runs ONLY on a file whose hash changed. It answers
    "did the candidate weaken this check", and an untouched marker answers
    nothing — a repository that already contains `@pytest.mark.skip` or
    `@pytest.mark.xfail` is normal, and scanning unconditionally rejected every
    candidate in it forever. That was survivable only while the protected set
    held nothing but the author's own fresh files; it is fatal once checks that
    were already in the repository are protected too.
    """
    reasons: list[str] = []
    for path_text, expected_hash in protected.items():
        path = root / path_text
        if not path.is_file():
            reasons.append(f"protected test missing or renamed: {path_text}")
            continue
        if sha256_file(path) == expected_hash:
            continue  # untouched: nothing to weaken, nothing to scan
        reasons.append(f"protected test changed: {path_text}")
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in _BYPASS_MARKERS):
            reasons.append(f"test bypass marker present: {path_text}")
    return reasons


def check_config_untouched(changed_paths: Iterable[str], exempt: Set[str]) -> list[str]:
    """Verification configuration must not move under either stage's feet.

    Shared, because weakening collection (`pytest.ini`, `pyproject.toml`) makes a
    green result meaningless no matter which stage did it.
    """
    reasons: list[str] = []
    for changed_path in sorted({Path(path).as_posix() for path in changed_paths}):
        if changed_path in exempt:
            continue
        if Path(changed_path).name in _CONFIG_NAMES:
            reasons.append(f"verification configuration changed: {changed_path}")
    return reasons


def check_declared_scope(
    contract: TaskContract, changed_paths: Iterable[str], exempt: Set[str]
) -> list[str]:
    """`allowed_scope` / `forbidden_scope` — the IMPLEMENTATION stage only.

    Deliberately not shared with authoring, and the reason is worth stating: the
    conventional way to keep the implementer out of the tests is
    `forbidden_scope: ["tests/"]`, which is precisely where the author has to
    write. The two stages have mirror-image legal areas, so one scope check
    cannot serve both. The author's legal area is "check paths only", enforced by
    `is_check_path` at the call site, plus the pre-existing checks it may not
    touch.
    """
    allowed = tuple(contract.allowed_scope)
    forbidden = tuple(contract.forbidden_scope)
    reasons: list[str] = []
    for changed_path in sorted({Path(path).as_posix() for path in changed_paths}):
        if changed_path in exempt:
            continue
        if forbidden and any(_matches_scope(changed_path, prefix) for prefix in forbidden):
            reasons.append(f"forbidden path changed: {changed_path}")
        if allowed and not any(_matches_scope(changed_path, prefix) for prefix in allowed):
            reasons.append(f"path outside allowed scope: {changed_path}")
    return reasons


def _verdict(*reason_groups: list[str]) -> CandidateValidation:
    reasons = [reason for group in reason_groups for reason in group]
    return CandidateValidation(not reasons, tuple(dict.fromkeys(reasons)))


def validate_candidate(
    root: Path,
    contract: TaskContract,
    bundle: TestBundle,
    changed_paths: Iterable[str],
) -> CandidateValidation:
    """The IMPLEMENTATION stage's verdict on a candidate tree."""
    identity = (
        []
        if bundle.validates(contract.id, contract.revision)
        else ["test bundle does not match task revision"]
    )
    return _verdict(
        identity,
        check_protected(root, bundle.protected_file_hashes),
        check_config_untouched(changed_paths, set(bundle.protected_file_hashes)),
        check_declared_scope(contract, changed_paths, set(bundle.protected_file_hashes)),
    )


def validate_authoring(
    root: Path,
    existing_checks: Mapping[str, str],
    changed_paths: Iterable[str],
) -> CandidateValidation:
    """The TEST-AUTHORING stage's verdict.

    Two rules, both previously absent — the stage was judged by `is_check_path`
    alone, so an author could rewrite `pytest.ini` to weaken collection, or edit
    another task's check and have the modified file hashed as this task's own
    protected evidence.

    `allowed_scope`/`forbidden_scope` are NOT applied here; see
    `check_declared_scope` for why they cannot be. No bundle exists yet either,
    hence a plain mapping — the rules only ever needed the hashes.
    """
    return _verdict(
        check_protected(root, existing_checks),
        check_config_untouched(changed_paths, set(existing_checks)),
    )
