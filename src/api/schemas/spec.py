"""src/api/schemas/spec.py — ProjectSpec API DTOs."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SpecResponse(BaseModel):
    """Flattened read-only view of the active ProjectSpec."""
    project_name: str
    version: str
    objective_description: str
    objective_domain: str
    forbidden_patterns: list[str]


# ── Propose Spec Change ───────────────────────────────────────────────────────

class ProposeSpecChangeRequest(BaseModel):
    new_version: Optional[str] = None
    new_objective_desc: Optional[str] = Field(default=None, max_length=2000)
    new_objective_domain: Optional[str] = Field(default=None, max_length=500)
    add_forbidden: list[str] = Field(default_factory=list)
    remove_forbidden: list[str] = Field(default_factory=list)
    add_required: list[str] = Field(default_factory=list)
    remove_required: list[str] = Field(default_factory=list)
    add_directory: Optional[dict[str, str]] = None
    remove_directory: Optional[str] = None
    rationale: str = Field(default="", max_length=1000)


class ProposeSpecChangeResponse(BaseModel):
    accepted: bool
    proposal_path: Optional[str]
    rejection_reason: Optional[str] = None


# ── Validate Against Spec ─────────────────────────────────────────────────────

class ValidateSpecRequest(BaseModel):
    artifact: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "The artifact text to validate (e.g. a task description, "
            "file path, or dependency declaration)."
        ),
    )


class ValidateSpecResponse(BaseModel):
    passed: bool
    violations: list[str]
    warnings: list[str]
