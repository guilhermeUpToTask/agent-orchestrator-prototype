"""
src/domain/project_spec/value_objects.py — Value objects for ProjectSpec.

SpecVersion   — semantic version wrapper that enforces the semver contract.
TechStack     — immutable description of backend / database / infra choices.
SpecConstraints — forbidden and required patterns.
DirectoryRule — structural rule for a single directory.
SpecObjective — project goal and domain description.
"""
from __future__ import annotations

import re
from typing import Tuple

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# SpecVersion
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)


class SpecVersion(BaseModel):
    """
    Semantic version value object.

    Accepts strings of the form MAJOR.MINOR.PATCH.
    Comparison operators follow numeric semver ordering.

    Immutable: use bump_patch() / bump_minor() / bump_major() to derive a
    new version rather than mutating the existing one.
    """

    raw: str

    model_config = {"frozen": True}

    @field_validator("raw")
    @classmethod
    def _must_be_semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(
                f"SpecVersion must follow MAJOR.MINOR.PATCH (semver); got '{v}'"
            )
        return v

    # ------------------------------------------------------------------
    # Parsed accessors
    # ------------------------------------------------------------------

    def _parts(self) -> Tuple[int, int, int]:
        m = _SEMVER_RE.match(self.raw)
        assert m is not None
        return int(m["major"]), int(m["minor"]), int(m["patch"])

    @property
    def major(self) -> int:
        return self._parts()[0]

    @property
    def minor(self) -> int:
        return self._parts()[1]

    @property
    def patch(self) -> int:
        return self._parts()[2]

    # ------------------------------------------------------------------
    # Bump helpers (return new instances — never mutate)
    # ------------------------------------------------------------------

    def bump_patch(self) -> "SpecVersion":
        ma, mi, pa = self._parts()
        return SpecVersion(raw=f"{ma}.{mi}.{pa + 1}")

    def bump_minor(self) -> "SpecVersion":
        ma, mi, _ = self._parts()
        return SpecVersion(raw=f"{ma}.{mi + 1}.0")

    def bump_major(self) -> "SpecVersion":
        ma, _, _ = self._parts()
        return SpecVersion(raw=f"{ma + 1}.0.0")

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def __lt__(self, other: "SpecVersion") -> bool:
        return self._parts() < other._parts()

    def __le__(self, other: "SpecVersion") -> bool:
        return self._parts() <= other._parts()

    def __gt__(self, other: "SpecVersion") -> bool:
        return self._parts() > other._parts()

    def __ge__(self, other: "SpecVersion") -> bool:
        return self._parts() >= other._parts()

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"SpecVersion('{self.raw}')"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def initial(cls) -> "SpecVersion":
        """Return the canonical starting version for a new spec."""
        return cls(raw="0.1.0")

    @classmethod
    def from_string(cls, value: str) -> "SpecVersion":
        return cls(raw=value)


# ---------------------------------------------------------------------------
# Structural schema value objects
# ---------------------------------------------------------------------------


class TechStack(BaseModel):
    """Immutable record of technology choices for each infrastructure tier."""

    backend: tuple[str, ...] = Field(default_factory=tuple)
    database: tuple[str, ...] = Field(default_factory=tuple)
    infra: tuple[str, ...] = Field(default_factory=tuple)

    model_config = {"frozen": True}

    @field_validator("backend", "database", "infra", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, v: object) -> tuple:
        if isinstance(v, (list, tuple)):
            return tuple(str(x) for x in v)
        if v is None:
            return ()
        raise ValueError(f"Expected list/tuple, got {type(v)}")


class SpecConstraints(BaseModel):
    """
    Two-sided constraint lens:
      forbidden — patterns that must never appear in the codebase
      required  — patterns that must always be present
    """

    forbidden: tuple[str, ...] = Field(default_factory=tuple)
    required: tuple[str, ...] = Field(default_factory=tuple)

    model_config = {"frozen": True}

    @field_validator("forbidden", "required", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, v: object) -> tuple:
        if isinstance(v, (list, tuple)):
            return tuple(str(x) for x in v)
        if v is None:
            return ()
        raise ValueError(f"Expected list/tuple, got {type(v)}")


class DirectoryRule(BaseModel):
    """Declares an expected directory and its architectural purpose."""

    name: str
    purpose: str

    model_config = {"frozen": True}


class StructureSpec(BaseModel):
    """Collection of directory rules that describe the project layout."""

    directories: tuple[DirectoryRule, ...] = Field(default_factory=tuple)

    model_config = {"frozen": True}

    @field_validator("directories", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, v: object) -> tuple:
        if isinstance(v, (list, tuple)):
            return tuple(
                DirectoryRule(**x) if isinstance(x, dict) else x for x in v
            )
        if v is None:
            return ()
        raise ValueError(f"Expected list/tuple, got {type(v)}")


class SpecObjective(BaseModel):
    """High-level statement of what this project is trying to achieve."""

    description: str
    domain: str

    model_config = {"frozen": True}
