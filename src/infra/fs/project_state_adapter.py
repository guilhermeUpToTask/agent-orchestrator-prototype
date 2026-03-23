"""
src/infra/fs/project_state_adapter.py — Filesystem adapter for ProjectStatePort.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.domain.ports.project_state import DecisionEntry, ProjectStatePort, SpecChanges

_FRONTMATTER_SEP = "---"


def _render_decision(entry: DecisionEntry) -> str:
    """Serialise a DecisionEntry to YAML frontmatter + content."""
    lines = [
        _FRONTMATTER_SEP,
        f"id: {entry.id}",
        f"date: {entry.date}",
        f"status: {entry.status}",
        f"domain: {entry.domain}",
        f"feature_tag: {entry.feature_tag}",
        f"superseded_by: {entry.superseded_by or ''}",
    ]
    # Serialize spec_changes if present
    if entry.spec_changes is not None:
        lines.append("spec_changes:")
        lines.append(f"  add_required: [{', '.join(entry.spec_changes.add_required)}]")
        lines.append(f"  add_forbidden: [{', '.join(entry.spec_changes.add_forbidden)}]")
        lines.append(f"  remove_required: [{', '.join(entry.spec_changes.remove_required)}]")
        lines.append(f"  remove_forbidden: [{', '.join(entry.spec_changes.remove_forbidden)}]")
    lines.append(_FRONTMATTER_SEP)
    lines.append(entry.content)
    return "\n".join(lines)


def _parse_decision(text: str) -> DecisionEntry:
    """Parse YAML frontmatter + content into a DecisionEntry."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_SEP:
        raise ValueError("Decision file missing frontmatter separator")
    end_idx = next(
        (i for i, l in enumerate(lines[1:], 1) if l.strip() == _FRONTMATTER_SEP),
        None,
    )
    if end_idx is None:
        raise ValueError("Decision file frontmatter not closed")
    fm_lines = lines[1:end_idx]
    content = "\n".join(lines[end_idx + 1:])
    
    # Parse frontmatter key-value pairs
    meta: dict[str, str] = {}
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        if ":" in line and not line.strip().startswith(" "):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        i += 1
    
    # Parse spec_changes if present
    spec_changes = None
    if "spec_changes:" in fm_lines:
        sc_lines = [l for l in fm_lines if l.strip().startswith("add_required:") 
                    or l.strip().startswith("add_forbidden:")
                    or l.strip().startswith("remove_required:")
                    or l.strip().startswith("remove_forbidden:")]
        sc_dict = {}
        for line in sc_lines:
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip().replace("  ", "").strip()
                # Parse list like "[fastapi, django]"
                v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    v = v[1:-1]
                    items = [item.strip() for item in v.split(",") if item.strip()]
                else:
                    items = [v] if v else []
                sc_dict[k] = items
        
        spec_changes = SpecChanges(
            add_required=sc_dict.get("add_required", []),
            add_forbidden=sc_dict.get("add_forbidden", []),
            remove_required=sc_dict.get("remove_required", []),
            remove_forbidden=sc_dict.get("remove_forbidden", []),
        )
    
    return DecisionEntry(
        id=meta.get("id", ""),
        date=meta.get("date", ""),
        status=meta.get("status", "active"),
        domain=meta.get("domain", ""),
        feature_tag=meta.get("feature_tag", ""),
        content=content,
        superseded_by=meta.get("superseded_by") or None,
        spec_changes=spec_changes,
    )


class FilesystemProjectStateAdapter(ProjectStatePort):
    """Stores project state documents as .md files under a dedicated directory."""

    def __init__(self, state_dir: str | Path) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._decisions_dir = self._dir / "decisions"
        self._decisions_dir.mkdir(exist_ok=True)

    # -- free-form keys --

    def read_state(self, key: str) -> Optional[str]:
        path = self._key_path(key)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_state(self, key: str, content: str) -> None:
        _atomic_write(self._key_path(key), content)

    def list_keys(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.md"))

    def delete_state(self, key: str) -> bool:
        path = self._key_path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    # -- structured decisions --

    def write_decision(self, entry: DecisionEntry) -> None:
        path = self._decision_path(entry.id)
        _atomic_write(path, _render_decision(entry))

    def list_decisions(
        self,
        domain: Optional[str] = None,
        status: str = "active",
    ) -> list[DecisionEntry]:
        results: list[DecisionEntry] = []
        for path in sorted(self._decisions_dir.glob("*.md")):
            try:
                entry = _parse_decision(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if status is not None and entry.status != status:
                continue
            if domain is not None and entry.domain != domain:
                continue
            results.append(entry)
        return results

    def supersede_decision(self, id: str, superseded_by: str, reason: str) -> bool:
        path = self._decision_path(id)
        if not path.exists():
            return False
        entry = _parse_decision(path.read_text(encoding="utf-8"))
        updated = DecisionEntry(
            id=entry.id,
            date=entry.date,
            status="superseded",
            domain=entry.domain,
            feature_tag=entry.feature_tag,
            content=entry.content + f"\n\n**Superseded by {superseded_by}**: {reason}",
            superseded_by=superseded_by,
        )
        _atomic_write(path, _render_decision(updated))
        return True

    # -- helpers --

    def _key_path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self._dir / f"{safe}.md"

    def _decision_path(self, decision_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in decision_id)
        return self._decisions_dir / f"{safe}.md"


# ---------------------------------------------------------------------------
# In-memory adapter (tests / dry-run)
# ---------------------------------------------------------------------------

class InMemoryProjectStateAdapter(ProjectStatePort):
    """Volatile in-memory adapter for tests and dry-run mode."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._decisions: dict[str, DecisionEntry] = {}

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

    def write_decision(self, entry: DecisionEntry) -> None:
        self._decisions[entry.id] = entry

    def list_decisions(
        self,
        domain: Optional[str] = None,
        status: str = "active",
    ) -> list[DecisionEntry]:
        results = []
        for entry in self._decisions.values():
            if status is not None and entry.status != status:
                continue
            if domain is not None and entry.domain != domain:
                continue
            results.append(entry)
        return sorted(results, key=lambda e: e.id)

    def supersede_decision(self, id: str, superseded_by: str, reason: str) -> bool:
        if id not in self._decisions:
            return False
        entry = self._decisions[id]
        self._decisions[id] = DecisionEntry(
            id=entry.id,
            date=entry.date,
            status="superseded",
            domain=entry.domain,
            feature_tag=entry.feature_tag,
            content=entry.content + f"\n\n**Superseded by {superseded_by}**: {reason}",
            superseded_by=superseded_by,
        )
        return True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
