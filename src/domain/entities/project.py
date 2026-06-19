"""
src/domain/entities/project.py — Project entity.

A Project owns only its repository coordinates and, transitively, its tasks.
Agents and model providers are GLOBAL and are referenced by id, never owned by
a project. The GitHub token is referenced via a SecretRef, never stored inline.

``state_version`` supports optimistic concurrency at the store boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from src.domain.value_objects.config import SecretRef


class Project(BaseModel):
    id: str
    name: str
    repo_url: str
    default_branch: str = "main"
    github_secret_ref: SecretRef | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state_version: int = 0
