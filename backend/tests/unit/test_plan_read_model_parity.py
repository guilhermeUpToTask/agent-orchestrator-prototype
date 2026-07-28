"""The hand-declared frontend read model must not drift from the API's.

`GET /api/plans/{plan_id}` is the aggregate document every view reads, and its
TypeScript shape is written by hand (`frontend/src/types/ui.ts`) rather than
generated — a deliberate choice recorded in CLAUDE.md, because the document is
assembled from the domain plus two projections. The cost of that choice is
silent drift, and drift here is invisible: a field the backend serves but the
read model never declares cannot be rendered without a type error, so the
feature behind it is simply unreachable.

Found by the Phase 3 audit: `provider_waiting` had been served since the
capacity work and was never declared, which made Phase 5's rule — distinguish
"waiting, recovering automatically" from "needs you" — unsatisfiable in the UI.

Only TOP-LEVEL field names are compared. Nested shapes are the generated types'
job; what this locks is that no field can be added to the response without the
frontend being told it exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.api.routers.plans import PlanDetailResponse

UI_TYPES = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "types" / "ui.ts"
)

# A top-level member of `export interface Plan { … }`: exactly two spaces of
# indent, so members of an inline nested object (four spaces) are skipped.
_MEMBER = re.compile(r"^ {2}(\w+)\??:", re.MULTILINE)


def _declared_fields() -> set[str]:
    source = UI_TYPES.read_text()
    start = source.index("export interface Plan {")
    end = source.index("\n}", start)
    return set(_MEMBER.findall(source[start:end]))


def test_the_read_model_declares_every_field_the_api_serves() -> None:
    undeclared = sorted(set(PlanDetailResponse.model_fields) - _declared_fields())

    assert undeclared == [], (
        "PlanDetailResponse serves fields the frontend read model never declares, "
        "so no component can render them: "
        f"{undeclared} — add them to the Plan interface in frontend/src/types/ui.ts"
    )


def test_the_read_model_declares_nothing_the_api_stopped_serving() -> None:
    phantom = sorted(_declared_fields() - set(PlanDetailResponse.model_fields))

    assert phantom == [], (
        "the frontend read model declares fields the API no longer serves, which "
        f"type-check as present and arrive undefined at runtime: {phantom}"
    )
