from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReviewParser:
    def parse_lessons(self, roadmap_data: dict[str, Any] | None) -> str:
        if not roadmap_data:
            return ""
        lessons = roadmap_data.get("lessons", "")
        return lessons if isinstance(lessons, str) else ""

    def parse_architecture_summary(self, roadmap_data: dict[str, Any] | None) -> str:
        if not roadmap_data:
            return ""
        summary = roadmap_data.get("architecture_summary", "")
        return summary if isinstance(summary, str) else ""
