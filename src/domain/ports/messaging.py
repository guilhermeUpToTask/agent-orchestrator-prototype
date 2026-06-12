"""
src/domain/ports/messaging.py — Event bus port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterator, Optional

from src.domain.events.domain_event import DomainEvent


class EventPort(ABC):
    """
    Contract for publishing and subscribing to domain events.
    Infrastructure provides adapters (Redis Streams, in-memory, etc.).
    """

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish event.

        Payloads must be minimal: IDs plus immutable result facts (e.g. the
        task.execution_* events carry branch/commit_sha/modified_files so the
        task manager can persist outcomes without trusting worker state).
        Anything mutable must be fetched from a repository by the consumer.
        """
        ...

    @abstractmethod
    def subscribe(
        self,
        event_type: str,
        group: str,
        consumer: str,
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to a single event type using consumer groups.
        Each message is delivered to exactly one consumer in the group.

        When *stop* is provided, the generator returns (instead of blocking
        forever) shortly after stop() turns true — required for running
        subscription loops on threads that must shut down cleanly.
        """
        ...

    @abstractmethod
    def subscribe_many(
        self,
        event_types: list[str],
        group: str,
        consumer: str,
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to multiple event types in a single call.
        Prefer this over chaining subscribe() calls — chaining blocking
        generators means only the first stream is ever consumed.

        *stop* semantics match subscribe().
        """
        ...

    @abstractmethod
    def ack(self, event: DomainEvent, group: str) -> None:
        """
        Acknowledge successful processing of *event* for consumer *group*.

        Redis-backed adapters use this to mark stream messages as processed.
        In-memory adapters may implement this as a no-op.
        """
        ...
