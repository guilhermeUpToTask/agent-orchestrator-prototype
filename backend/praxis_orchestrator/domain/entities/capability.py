from __future__ import annotations
from pydantic import BaseModel


class Capability(BaseModel):
    """A named capability an agent can satisfy, bundling the tools it implies."""

    id: str
    name: str
    description: str
    tools: list[str] = []
