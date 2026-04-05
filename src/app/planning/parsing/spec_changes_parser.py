from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from src.domain.ports.project_state import SpecChanges


class SpecChangesParseError(ValueError):
    """Raised when a spec_changes payload is malformed."""


@dataclass(frozen=True)
class SpecChangesParser:
    """Parse spec_changes payloads into typed SpecChanges objects."""

    def parse(self, spec_changes_json: Optional[str]) -> Optional[SpecChanges]:
        if not spec_changes_json:
            return None

        try:
            data = json.loads(spec_changes_json)
        except json.JSONDecodeError as exc:
            raise SpecChangesParseError(f"Invalid JSON for spec_changes_json: {exc}") from exc

        if not isinstance(data, dict):
            raise SpecChangesParseError("spec_changes_json must decode to a JSON object")

        return SpecChanges(
            add_required=self._parse_list(data, "add_required"),
            add_forbidden=self._parse_list(data, "add_forbidden"),
            remove_required=self._parse_list(data, "remove_required"),
            remove_forbidden=self._parse_list(data, "remove_forbidden"),
        )

    def _parse_list(self, data: dict, key: str) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list):
            raise SpecChangesParseError(f"'{key}' must be a list")
        if not all(isinstance(item, str) for item in value):
            raise SpecChangesParseError(f"'{key}' must contain only strings")
        return value
