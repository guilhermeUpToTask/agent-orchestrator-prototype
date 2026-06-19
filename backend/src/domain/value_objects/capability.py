"""
src/domain/value_objects/capability.py — Capability tags.

A capability tag is the typed vocabulary shared by every layer: agents declare
the tags they hold, tasks declare the single tag they require, and the
SchedulerService matches the two. Tags are validated for *format* by this value
object; *membership* (is this a known/registered tag?) is enforced at the
boundaries against the CapabilityRegistryPort — the type cannot reach the
registry, and the registry is intentionally dynamic.

Style: lowercase, optionally namespaced with ':' (e.g. ``code:backend``,
``test:write``, ``review``). This is deliberately not a Python Enum so new tags
can be registered at runtime without a code change.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

# Single segment (``review``, ``backend_dev``) or namespaced (``code:backend``,
# ``test:write``). Segments are lowercase alphanumerics with ``_``/``-``;
# namespaces are separated by ``:``. Rejects spaces, uppercase (normalized
# first), and other punctuation.
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(:[a-z0-9_-]+)*$")

# Self-healing migration of legacy free-form values to canonical tags. Applied
# on every construction so persisted data (e.g. tasks stamped ``coding``) reads
# back as the canonical tag and matches agents without a migration script.
_LEGACY_ALIASES = {
    "coding": "code:backend",
}

# Built-in tags seeded into a fresh project's CapabilityRegistry.
DEFAULT_CAPABILITIES: list[str] = [
    "code:backend",
    "code:frontend",
    "test:write",
    "review",
    "docs",
]


def normalize_capability(value: str) -> str:
    """Normalize and format-validate a capability tag.

    Lowercases/trims, maps legacy aliases, and enforces the tag grammar. Raises
    ``ValueError`` on a malformed tag (pydantic surfaces this as a validation
    error; CLI/registry callers catch it directly).
    """
    if not isinstance(value, str):
        raise ValueError(f"Capability tag must be a string, got {type(value).__name__}")
    tag = value.strip().lower()
    tag = _LEGACY_ALIASES.get(tag, tag)
    if not _TAG_RE.match(tag):
        raise ValueError(
            f"Invalid capability tag '{value}'. Use lowercase, optionally "
            "namespaced with ':' (e.g. 'code:backend', 'test:write', 'review')."
        )
    return tag


# A validated string alias: carrying CapabilityTag (not bare str) is the typed
# guarantee; normalization/format-validation runs wherever it is parsed.
CapabilityTag = Annotated[str, AfterValidator(normalize_capability)]
