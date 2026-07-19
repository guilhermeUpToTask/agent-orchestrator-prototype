"""Unit tests for process_supervisor: streaming, timeout, bounded JSONL log."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from src.infra.runtime.process_supervisor import (
    attempt_log_path,
    supervise_process,
)


def _env() -> dict[str, str]:
    return dict(os.environ)


def test_attempt_log_path_is_deterministic(tmp_path: Path) -> None:
    first = attempt_log_path(tmp_path, "attempt-abc")
    second = attempt_log_path(tmp_path, "attempt-abc")
    other = attempt_log_path(tmp_path, "attempt-xyz")

    assert first == second
    assert first == tmp_path / "runtime-logs" / "attempt-abc.jsonl"
    assert other != first


def test_supervise_process_streams_output_incrementally(tmp_path: Path) -> None:
    """on_output must fire as lines arrive, not only once at process exit."""
    script = (
        "import sys, time\n"
        "print('line-one', flush=True)\n"
        "time.sleep(0.15)\n"
        "print('line-two', flush=True)\n"
        "time.sleep(0.15)\n"
        "print('line-three', flush=True)\n"
    )
    callbacks: list[tuple[str, str, float]] = []

    def on_output(stream: str, text: str) -> None:
        callbacks.append((stream, text, time.monotonic()))

    result = supervise_process(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=_env(),
        timeout_seconds=10,
        log_path=tmp_path / "stream.jsonl",
        on_output=on_output,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "line-one" in result.stdout
    assert "line-two" in result.stdout
    assert "line-three" in result.stdout

    stdout_calls = [c for c in callbacks if c[0] == "stdout"]
    assert len(stdout_calls) > 1, (
        f"expected incremental callbacks, got {len(stdout_calls)}: {stdout_calls!r}"
    )
    # successive callbacks must be spaced by the script's sleeps (not batched at end)
    gaps = [
        stdout_calls[i + 1][2] - stdout_calls[i][2] for i in range(len(stdout_calls) - 1)
    ]
    assert any(gap >= 0.05 for gap in gaps), f"callbacks looked batched: gaps={gaps!r}"

    log_lines = (tmp_path / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) >= 3
    records = [json.loads(line) for line in log_lines]
    texts = [r["text"] for r in records if "text" in r]
    assert any("line-one" in t for t in texts)
    assert any("line-three" in t for t in texts)


def test_supervise_process_times_out_and_terminates(tmp_path: Path) -> None:
    script = "import time; time.sleep(30)"
    started = time.monotonic()

    result = supervise_process(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=_env(),
        timeout_seconds=1,
        log_path=tmp_path / "timeout.jsonl",
    )

    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert elapsed < 10, f"process was not terminated promptly: {elapsed:.1f}s"
    # killed processes typically leave a non-zero / negative exit code on POSIX
    assert result.exit_code != 0


def test_supervise_process_caps_log_and_inserts_truncation_marker(tmp_path: Path) -> None:
    # each line is large enough that a few exceed a small cap
    line = "x" * 200
    script = (
        "import sys\n"
        f"for i in range(20):\n"
        f"    print({line!r} + str(i), flush=True)\n"
    )
    log_path = tmp_path / "capped.jsonl"
    cap = 1024

    result = supervise_process(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=_env(),
        timeout_seconds=10,
        log_path=log_path,
        log_cap_bytes=cap,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    raw = log_path.read_bytes()
    assert len(raw) <= cap

    lines = raw.decode("utf-8").splitlines()
    assert any(line.strip() == '{"truncated":true}' for line in lines), lines

    # surviving content is the newest lines (old content dropped)
    text_payloads: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "text" in record:
            text_payloads.append(record["text"])
    assert text_payloads, "expected some retained log entries after truncation"
    # earliest lines must be gone; latest indices should remain
    assert not any(t.rstrip().endswith("0") for t in text_payloads)
    assert any(t.rstrip().endswith(("18", "19")) for t in text_payloads)

def test_supervise_process_bounds_retained_output_but_counts_all_bytes(tmp_path: Path) -> None:
    script = "import sys\nfor i in range(20):\n    print('line-' + str(i) + ':' + 'x' * 100, flush=True)\n"
    cap = 512

    result = supervise_process(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=_env(),
        timeout_seconds=10,
        log_path=tmp_path / "bounded-output.jsonl",
        log_cap_bytes=cap,
    )

    assert len(result.stdout.encode("utf-8")) <= cap
    assert result.stdout_bytes > cap
    assert result.stdout
    assert all(line.startswith("line-") for line in result.stdout.splitlines())
    assert "line-19:" in result.stdout


def test_supervise_process_rejects_tiny_log_cap(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="log cap"):
        supervise_process(
            [sys.executable, "-c", "print('hi')"],
            cwd=str(tmp_path),
            env=_env(),
            timeout_seconds=5,
            log_path=tmp_path / "tiny.jsonl",
            log_cap_bytes=64,
        )
