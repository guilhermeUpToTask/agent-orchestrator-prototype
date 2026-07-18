from __future__ import annotations
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, TextIO
from src.app.observations import (
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationSource,
    ProcessObservationPayload,
    TelemetryObservation,
)

StreamName = Literal["stdout", "stderr"]
OutputCallback = Callable[[StreamName, str], None]
ObservationCallback = Callable[[TelemetryObservation], None]


def attempt_log_path(orchestrator_home: Path, attempt_id: str) -> Path:
    """Return the durable runtime log location for an execution attempt."""
    return orchestrator_home / "runtime-logs" / f"{attempt_id}.jsonl"


@dataclass(frozen=True)
class ProcessSupervisorResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    log_path: Path
    stdout_bytes: int
    stderr_bytes: int
    duration_seconds: float


class _BoundedLog:
    def __init__(self, path: Path, cap_bytes: int) -> None:
        if cap_bytes < 256:
            raise ValueError("log cap must be at least 256 bytes")
        self.path, self.cap_bytes, self._lock = path, cap_bytes, threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def write(self, stream: StreamName, chunk: str) -> None:
        record = (
            json.dumps(
                {"monotonic_seconds": time.monotonic(), "stream": stream, "text": chunk},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        marker = b'{"truncated":true}\n'
        with self._lock, self.path.open("ab+") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() + len(record) > self.cap_bytes:
                handle.seek(0)
                existing = handle.read()
                # Keep the marker plus the newest complete lines that still fit
                # alongside the incoming record.
                budget = max(0, self.cap_bytes - len(record) - len(marker))
                if budget == 0:
                    retained = marker if len(marker) + min(len(record), self.cap_bytes) <= self.cap_bytes else b""
                else:
                    tail = existing[-budget:]
                    # Drop a leading partial line when the byte cut lands mid-record.
                    nl = tail.find(b"\n")
                    if 0 <= nl < len(tail) - 1:
                        tail = tail[nl + 1 :]
                    elif nl == len(tail) - 1:
                        tail = b""
                    retained = marker + tail
                handle.seek(0)
                handle.truncate()
                handle.write(retained)
            handle.write(record[-self.cap_bytes :])


def terminate_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def supervise_process(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_seconds: int,
    log_path: Path | None = None,
    log_cap_bytes: int = 1048576,
    on_output: OutputCallback | None = None,
    on_observation: ObservationCallback | None = None,
    plan_id: str = "process-supervisor",
    goal_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    attempt_id: str | None = None,
    attempt_number: int | None = None,
) -> ProcessSupervisorResult:
    if log_path is None:
        fd, raw_path = tempfile.mkstemp(prefix="orchestrator-process-", suffix=".log")
        os.close(fd)
        log_path = Path(raw_path)
    log, started_at = _BoundedLog(log_path, log_cap_bytes), time.monotonic()

    def observe(kind: ObservationKind, payload: ProcessObservationPayload) -> None:
        if on_observation is not None:
            on_observation(
                TelemetryObservation(
                    correlation=ObservationCorrelation(
                        plan_id=plan_id,
                        goal_id=goal_id,
                        task_id=task_id,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=attempt_number,
                    ),
                    observed_at=datetime.now(timezone.utc),
                    source=ObservationSource.PROCESS,
                    quality=ObservationQuality.EXACT,
                    kind=kind,
                    payload=payload,
                )
            )

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    observe(ObservationKind.PROCESS_STARTED, ProcessObservationPayload(log_path=str(log_path)))
    outputs: dict[StreamName, deque[str]] = {"stdout": deque(), "stderr": deque()}
    retained_bytes: dict[StreamName, int] = {"stdout": 0, "stderr": 0}
    counts: dict[StreamName, int] = {"stdout": 0, "stderr": 0}

    def retain(stream: StreamName, chunk: str) -> None:
        outputs[stream].append(chunk)
        retained_bytes[stream] += len(chunk.encode("utf-8"))
        while retained_bytes[stream] > log_cap_bytes:
            oldest = outputs[stream].popleft()
            retained_bytes[stream] -= len(oldest.encode("utf-8"))

    def read_stream(stream: StreamName, pipe: TextIO | None) -> None:
        if pipe is None:
            return
        while True:
            chunk = pipe.readline()
            if not chunk:
                return
            retain(stream, chunk)
            counts[stream] += len(chunk.encode("utf-8"))
            log.write(stream, chunk)
            if on_output is not None:
                on_output(stream, chunk)

    threads = [
        threading.Thread(target=read_stream, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", proc.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_group(proc)
    finally:
        if not timed_out:
            terminate_process_group(proc)
        for thread in threads:
            thread.join(timeout=2)
    duration = round(time.monotonic() - started_at, 6)
    observe(
        ObservationKind.PROCESS_TIMED_OUT if timed_out else ObservationKind.PROCESS_EXITED,
        ProcessObservationPayload(
            stdout_bytes=counts["stdout"],
            stderr_bytes=counts["stderr"],
            exit_code=proc.returncode,
            duration_seconds=duration,
            log_path=str(log_path),
        ),
    )
    return ProcessSupervisorResult(
        stdout="".join(outputs["stdout"]),
        stderr="".join(outputs["stderr"]),
        exit_code=proc.returncode,
        timed_out=timed_out,
        log_path=log_path,
        stdout_bytes=counts["stdout"],
        stderr_bytes=counts["stderr"],
        duration_seconds=duration,
    )
