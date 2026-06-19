"""
src/infra/fs/capability_registry.py — JSON filesystem adapter for
CapabilityRegistryPort.

Stores the known capability tags as a JSON array. Reads fresh on every call
(so a tag added by one process is immediately visible to another) and writes
atomically, mirroring JsonAgentRegistry.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.domain import CapabilityRegistryPort, normalize_capability


class JsonCapabilityRegistry(CapabilityRegistryPort):
    def __init__(self, registry_path: str | Path) -> None:
        self._path = Path(registry_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write([])

    def list_tags(self) -> list[str]:
        return sorted(self._read())

    def add(self, tag: str) -> None:
        normalized = normalize_capability(tag)
        tags = self._read()
        if normalized not in tags:
            tags.add(normalized)
            self._write(sorted(tags))

    def remove(self, tag: str) -> None:
        normalized = normalize_capability(tag)
        tags = self._read()
        if normalized in tags:
            tags.discard(normalized)
            self._write(sorted(tags))

    def exists(self, tag: str) -> bool:
        try:
            normalized = normalize_capability(tag)
        except ValueError:
            return False
        return normalized in self._read()

    def ensure_defaults(self, defaults: list[str]) -> None:
        tags = self._read()
        missing = {normalize_capability(d) for d in defaults} - tags
        if missing:
            self._write(sorted(tags | missing))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read(self) -> set[str]:
        return set(json.loads(self._path.read_text()))

    def _write(self, tags: list[str]) -> None:
        from src.infra.fs.atomic_writer import AtomicFileWriter

        AtomicFileWriter.write_text(self._path, json.dumps(tags, indent=2))
