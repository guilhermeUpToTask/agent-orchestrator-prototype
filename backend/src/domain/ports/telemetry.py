from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.events.telemetry_event import TelemetryEvent


class TelemetryEmitterPort(ABC):
    """Hexagonal output port for telemetry persistence and logging."""

    @abstractmethod
    def emit(self, event: TelemetryEvent) -> None:
        ...
