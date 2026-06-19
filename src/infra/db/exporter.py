"""
src/infra/db/exporter.py — read-only SQLite -> dict/YAML snapshot.

A debugging/support aid only: it reads the authoritative SQLite config and emits
a human-inspectable snapshot. Secrets are represented by their refs and a masked
marker — never the plaintext. This is strictly one-directional; it is never a
write path (no dual source of truth).
"""
from __future__ import annotations

from typing import Any

import yaml

from src.domain.repositories.config_store import ConfigStorePort


def export_config(config: ConfigStorePort) -> dict[str, Any]:
    """Return a plain-dict snapshot of all global config (no secrets)."""
    return {
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "repo_url": p.repo_url,
                "default_branch": p.default_branch,
                "github_secret_ref": (
                    p.github_secret_ref.uri if p.github_secret_ref else None
                ),
                "state_version": p.state_version,
            }
            for p in config.list_projects()
        ],
        "providers": [
            {
                "id": pr.id,
                "kind": pr.kind.value,
                "secret_ref": pr.secret_ref.uri,
                "base_url": pr.base_url,
                "default_model": pr.default_model,
                "models": [
                    {"model_id": m.model_id, "display_name": m.display_name}
                    for m in pr.models
                ],
                "state_version": pr.state_version,
            }
            for pr in config.list_providers()
        ],
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "runtime_type": a.runtime_type,
                "provider_id": a.provider_id,
                "model_id": a.model_id,
                "capabilities": list(a.capabilities),
                "state_version": a.state_version,
            }
            for a in config.list_agents()
        ],
    }


def export_config_yaml(config: ConfigStorePort) -> str:
    return yaml.safe_dump(export_config(config), default_flow_style=False, sort_keys=False)
