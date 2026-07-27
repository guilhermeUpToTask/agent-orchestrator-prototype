"""The fixture's own verdict on a run. Never visible to the agent.

v1 ran `pytest` inside the repo — the same `tests/` the agent writes to. That
made the verdict circular: a weak test the agent wrote and then implemented to
would pass, and the fixture would call the run green. This file lives in the
fixture directory, is copied into a throwaway checkout by `check-success.sh`,
and is deleted afterwards, so no agent can read, weaken, or satisfy it by
construction.

Three questions, in increasing order of what they prove:

1. does the code do what the brief asked?
2. did the agent actually author a check?
3. does that check DISCRIMINATE — would it have caught a wrong implementation?

(3) is the one that matters. An agent can satisfy (1) and (2) with
`def test_greet(): greet("Ada")` — no assertion, always green. The mutation
probe below breaks `greet` on purpose in a scratch copy and requires the agent's
own tests to fail against it. A test that survives a broken implementation
proves nothing, and nothing inside the repo can detect that.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GREETER = Path("src/happy_path/greeter.py")
TESTS = Path("tests")


def _agent_tests(root: Path) -> list[Path]:
    return sorted(p for p in (root / TESTS).glob("test_*.py") if p.is_file())


def _run_pytest(root: Path, target: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(target or TESTS)],
        cwd=root,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(root / "src"),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(root),
        },
        check=False,
    )


# --- 1. the behaviour the brief asked for ---------------------------------


def test_greet_returns_the_promised_greeting() -> None:
    sys.path.insert(0, str(REPO / "src"))
    try:
        from happy_path.greeter import greet  # noqa: PLC0415 - path set above
    except ImportError as exc:  # pragma: no cover - reported as a failure
        pytest.fail(f"cannot import greet: {exc}")
    assert greet("Ada") == "Hello, Ada!"


# --- 2. the agent authored a check at all ---------------------------------


def test_the_agent_authored_a_check() -> None:
    authored = _agent_tests(REPO)
    assert authored, (
        "no tests/test_*.py in the promoted branch — the run produced an "
        "implementation with nothing proving it"
    )
    source = "\n".join(path.read_text(errors="replace") for path in authored)
    assert "greet" in source, "the authored tests never mention greet"


def test_the_authored_check_passes_on_the_real_implementation() -> None:
    result = _run_pytest(REPO)
    assert result.returncode == 0, (
        "the agent's own tests fail against its own implementation:\n"
        f"{result.stdout}\n{result.stderr}"
    )


# --- 3. the check discriminates -------------------------------------------


def test_the_authored_check_fails_against_a_broken_implementation() -> None:
    """The mutation probe.

    Replace `greet` with a version that returns the wrong thing and re-run the
    agent's own tests. If they still pass, the test is vacuous — it asserts
    nothing about the behaviour it claims to cover, and the green result above
    was meaningless.
    """
    assert _agent_tests(REPO), "no authored tests to probe"

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "mutant"
        shutil.copytree(
            REPO,
            scratch,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        (scratch / GREETER).write_text(
            'def greet(name: str) -> str:\n    """Deliberately wrong."""\n    return ""\n'
        )

        result = _run_pytest(scratch)

    assert result.returncode != 0, (
        "the agent's tests PASS against a greet() that returns the empty string.\n"
        "The check does not discriminate, so the green run proved nothing.\n"
        f"{result.stdout}\n{result.stderr}"
    )
