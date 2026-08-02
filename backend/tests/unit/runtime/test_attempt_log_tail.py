"""Resuming the raw attempt-log tail must not skip lines.

The route serves each event's offset as the SSE `id:`, and the client records
`id:` PER FRAME and reconnects with `?offset=`. So every frame's offset has to
mean "everything up to and including this line", not "everything the server
happened to read in that poll" — otherwise a disconnect between two frames of
one read resumes past the frames the client never received.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_orchestrator.infra.runtime.process_supervisor import follow_attempt_log

RECORDS = [
    {"monotonic_seconds": 0.1, "stream": "stdout", "text": "one"},
    {"monotonic_seconds": 0.2, "stream": "stderr", "text": "two"},
    {"monotonic_seconds": 0.3, "stream": "stdout", "text": "three"},
]


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _drain(path: Path, start_offset: int = 0) -> list:
    async def collect():
        return [
            event
            async for event in follow_attempt_log(
                path, is_terminal=lambda: True, start_offset=start_offset
            )
        ]

    return asyncio.run(collect())


def test_each_line_carries_its_own_resume_offset(tmp_path):
    path = tmp_path / "attempt.jsonl"
    _write(path, RECORDS)

    events = _drain(path)

    assert [event.record["text"] for event in events] == ["one", "two", "three"]
    offsets = [event.offset for event in events]
    assert len(set(offsets)) == 3, f"all three frames shared one offset: {offsets}"
    assert offsets == sorted(offsets)
    assert offsets[-1] == path.stat().st_size


def test_resuming_from_a_frame_replays_exactly_what_followed_it(tmp_path):
    """The disconnect this exists for: the client received frame 1 and dropped."""
    path = tmp_path / "attempt.jsonl"
    _write(path, RECORDS)

    first = _drain(path)[0]
    resumed = _drain(path, start_offset=first.offset)

    assert [event.record["text"] for event in resumed] == ["two", "three"]


def test_resuming_from_the_last_frame_replays_nothing(tmp_path):
    path = tmp_path / "attempt.jsonl"
    _write(path, RECORDS)

    last = _drain(path)[-1]

    assert _drain(path, start_offset=last.offset) == []


def test_a_multibyte_line_does_not_desynchronize_the_offsets(tmp_path):
    """Offsets are BYTE offsets the reader seeks to; the payload is decoded
    text. Counting decoded characters would drift on any non-ASCII output."""
    path = tmp_path / "attempt.jsonl"
    _write(
        path,
        [
            {"monotonic_seconds": 0.1, "stream": "stdout", "text": "café ☕"},
            {"monotonic_seconds": 0.2, "stream": "stdout", "text": "after"},
        ],
    )

    first = _drain(path)[0]

    assert [event.record["text"] for event in _drain(path, start_offset=first.offset)] == [
        "after"
    ]
