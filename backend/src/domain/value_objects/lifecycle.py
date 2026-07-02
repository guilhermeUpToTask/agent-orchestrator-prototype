"""Lifecycle value objects shared by goals and tasks.

`Status` lives here (not in tasks_vos) because it is shared by Goal and Task —
see DESIGN_NOTES #6. `FailureKind` is the SHARED FAILURE TAXONOMY (roadmap 2.4):
one constant set used by BOTH the real agent runner and the dummy, so tests
exercise the same retry/terminal classification production hits.
"""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    """Lifecycle state shared by goals and tasks. str-based so comparisons and JSON
    serialization are natural (task.status == Status.DONE works)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


# States the navigation scan treats as finished. A FAILED node being terminal is
# what prevents the infinite-loop: it is skipped, never re-selected forever.
TERMINAL: frozenset[Status] = frozenset({Status.DONE, Status.SKIPPED, Status.FAILED})


class FailureKind(str, Enum):
    """Typed classification of a task failure. Produced by the agent runner,
    consumed by RetryPolicy.should_retry — a checked classification, not string
    matching (DESIGN_NOTES #2)."""

    CONNECTION_ERROR = "connection_error"
    RATE_LIMIT = "rate_limit"
    TOKEN_LIMIT = "token_limit"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"
