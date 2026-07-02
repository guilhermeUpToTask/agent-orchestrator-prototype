from __future__ import annotations

import uuid


def new_id() -> str:
    """Single source of identity generation across factories, so the strategy
    (uuid now, something else later) is centralized."""
    return str(uuid.uuid4())
