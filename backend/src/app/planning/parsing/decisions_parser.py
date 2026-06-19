from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.app.planning.parsing.spec_changes_parser import SpecChangesParseError, SpecChangesParser
from src.domain.ports.project_state import DecisionEntry


class DecisionsParseError(ValueError):
    """Raised when pending_decisions payload is malformed."""


@dataclass(frozen=True)
class DecisionsParser:
    spec_changes_parser: SpecChangesParser

    def parse_pending(self, roadmap_data: dict[str, Any] | None) -> list[DecisionEntry]:
        if not roadmap_data:
            return []

        decisions_data = roadmap_data.get("pending_decisions", [])
        if not isinstance(decisions_data, list):
            raise DecisionsParseError("'pending_decisions' must be a list")

        entries: list[DecisionEntry] = []
        for idx, decision in enumerate(decisions_data):
            if not isinstance(decision, dict):
                raise DecisionsParseError(f"pending_decisions[{idx}] must be an object")
            for key in ("id", "domain", "content"):
                if not isinstance(decision.get(key), str) or not decision[key].strip():
                    raise DecisionsParseError(f"pending_decisions[{idx}] missing non-empty '{key}'")

            try:
                spec_changes = self.spec_changes_parser.parse(decision.get("spec_changes_json"))
            except SpecChangesParseError as exc:
                raise DecisionsParseError(str(exc)) from exc

            entries.append(
                DecisionEntry(
                    id=decision["id"],
                    date=decision.get("date", str(date.today())),
                    status=decision.get("status", "active"),
                    domain=decision["domain"],
                    feature_tag=decision.get("feature_tag", ""),
                    content=decision["content"],
                    spec_changes=spec_changes,
                )
            )
        return entries
