"""
src/infra/fs/project_state_adapter.py — Filesystem adapter for ProjectStatePort.

Stores each state document as a markdown file:
  ~/.orchestrator/<project>/project_state/<key>.md

Writes are atomic (temp-file + os.replace) so a crash mid-write never
leaves a partially-written document.  Reads return None for missing keys
rather than raising, matching the port contract.

Well-known keys written and read by the planner:
  "decisions"    — accumulated architectural decisions in markdown
  "current_arch" — current system architecture description
  "context"      — free-form project context the planner maintains
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.domain.ports.project_state import ProjectStatePort


class FilesystemProjectStateAdapter(ProjectStatePort):
    """
    Stores project state documents as .md files under a dedicated directory.
    """

    def __init__(self, state_dir: str | Path) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # ProjectStatePort
    # ------------------------------------------------------------------

    def read_state(self, key: str) -> Optional[str]:
        path = self._key_path(key)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_state(self, key: str, content: str) -> None:
        path = self._key_path(key)
        _atomic_write(path, content)

    def list_keys(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.md"))

    def delete_state(self, key: str) -> bool:
        path = self._key_path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _key_path(self, key: str) -> Path:
        # Sanitise key to prevent path traversal — keep only alphanum + hyphen/underscore.
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self._dir / f"{safe}.md"


# ---------------------------------------------------------------------------
# In-memory adapter (tests / dry-run)
# ---------------------------------------------------------------------------

class InMemoryProjectStateAdapter(ProjectStatePort):
    """
    Volatile in-memory adapter for tests and dry-run mode.
    No disk I/O, no persistence across process restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def read_state(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def write_state(self, key: str, content: str) -> None:
        self._store[key] = content

    def list_keys(self) -> list[str]:
        return sorted(self._store.keys())

    def delete_state(self, key: str) -> bool:
        if key not in self._store:
            return False
        del self._store[key]
        return True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path via a temp file + atomic rename."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
