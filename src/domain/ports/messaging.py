"""
src/domain/ports/messaging.py — Event bus port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from src.domain.events.domain_event import DomainEvent


class EventPort(ABC):
    """
    Contract for publishing and subscribing to domain events.
    Infrastructure provides adapters (Redis Streams, in-memory, etc.).
    """

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish event. Payload must be minimal — IDs only."""
        ...

    @abstractmethod
    def subscribe(
        self, event_type: str, group: str, consumer: str
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to a single event type using consumer groups.
        Each message is delivered to exactly one consumer in the group.
        """
        ...

    @abstractmethod
    def subscribe_many(
        self, event_types: list[str], group: str, consumer: str
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to multiple event types in a single call.
        Prefer this over chaining subscribe() calls — chaining blocking
        generators means only the first stream is ever consumed.
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
