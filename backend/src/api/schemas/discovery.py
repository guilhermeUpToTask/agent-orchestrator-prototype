"""src/api/schemas/discovery.py — Discovery session API DTOs.

Discovery runs as a long-lived session (see schemas/sessions.py): start
returns 202 + SessionAccepted, answers are POSTed to the session, and the
current question / final brief are read via the session GET endpoint.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveryMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
