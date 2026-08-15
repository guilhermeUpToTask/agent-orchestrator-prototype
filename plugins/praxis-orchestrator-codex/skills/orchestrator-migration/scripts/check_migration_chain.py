#!/usr/bin/env python3
"""Validate that Alembic revisions form one continuous chain."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
# Inside the package, not beside it: an installed copy has no repository to find
# them next to, so `backend/alembic/versions` stopped existing when the wheel
# started shipping its own schema.
VERSIONS = ROOT / "backend" / "praxis_orchestrator" / "infra" / "db" / "migrations" / "versions"


def literal_assignment(tree: ast.AST, name: str) -> str | None:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = ast.literal_eval(node.value)
            return value if isinstance(value, str) else None
    return None


def main() -> int:
    # A missing directory used to read out as "0 revisions, no head" — the same
    # failure a genuinely broken chain produces, which is how a moved migration
    # directory hid behind a chain-integrity error.
    if not VERSIONS.is_dir():
        print(f"No migration directory at {VERSIONS.relative_to(ROOT)}")
        return 1
    revisions: dict[str, tuple[str | None, Path]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revision = literal_assignment(tree, "revision")
        down = literal_assignment(tree, "down_revision")
        if not revision:
            print(f"{path.relative_to(ROOT)}: missing literal revision")
            return 1
        revisions[revision] = (down, path)
    referenced = {down for down, _ in revisions.values() if down}
    missing = sorted(referenced - revisions.keys())
    heads = sorted(set(revisions) - referenced)
    if missing or len(heads) != 1:
        print(f"Missing predecessors: {missing or 'none'}")
        print(f"Heads: {heads}")
        return 1
    print(f"Migration chain: {len(revisions)} revisions, head {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
