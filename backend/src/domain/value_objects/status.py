"""
src/domain/value_objects/status.py — Status value objects.

TaskStatus and TrustLevel are value objects: immutable, compared by value,
no identity of their own. They describe state and trust level at a point
in time and are always owned by an aggregate or entity.

The convenience sets on TaskStatus (assignable, active, terminal) are
value-level rules — which statuses belong together — so they live here,
not on the aggregate.
"""
from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    CREATED     = "created"
    ASSIGNED    = "assigned"
    IN_PROGRESS = "in_progress"
    SUCCEEDED   = "succeeded"
    FAILED      = "failed"
    CANCELED    = "canceled"
    REQUEUED    = "requeued"
    MERGED      = "merged"

    @classmethod
    def terminal(cls) -> frozenset["TaskStatus"]:
        """Statuses from which no further automatic transitions occur."""
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.CANCELED, cls.MERGED})

    @classmethod
    def assignable(cls) -> frozenset["TaskStatus"]:
        """Statuses from which a task can receive an agent assignment."""
        return frozenset({cls.CREATED, cls.REQUEUED})

    @classmethod
    def active(cls) -> frozenset["TaskStatus"]:
        """Statuses representing live work in progress."""
        return frozenset({cls.ASSIGNED, cls.IN_PROGRESS})


class TrustLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
