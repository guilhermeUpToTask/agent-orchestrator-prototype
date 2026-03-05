"""
src/infra/fs/agent_registry.py — JSON filesystem adapter for AgentRegistryPort.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.models import AgentProps
from src.core.ports import AgentRegistryPort


class JsonAgentRegistry(AgentRegistryPort):

    def __init__(self, registry_path: str | Path = "workflow/agents/registry.json") -> None:
        self._path = Path(registry_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({})

    def register(self, agent: AgentProps) -> None:
        data = self._read()
        data[agent.agent_id] = agent.model_dump(mode="json")
        self._write(data)

    def deregister(self, agent_id: str) -> None:
        data = self._read()
        data.pop(agent_id, None)
        self._write(data)

    def list_agents(self) -> list[AgentProps]:
        data = self._read()
        return [AgentProps.model_validate(v) for v in data.values()]

    def heartbeat(self, agent_id: str) -> None:
        data = self._read()
        if agent_id in data:
            data[agent_id]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
            self._write(data)

    def get(self, agent_id: str) -> Optional[AgentProps]:
        data = self._read()
        if agent_id not in data:
            return None
        return AgentProps.model_validate(data[agent_id])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        return json.loads(self._path.read_text())

    def _write(self, data: dict) -> None:
        content = json.dumps(data, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
