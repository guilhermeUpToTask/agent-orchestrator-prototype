#!/usr/bin/env python3
"""Validate that Alembic revisions form one continuous chain."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
VERSIONS = ROOT / "backend" / "alembic" / "versions"


def literal_assignment(tree: ast.AST, name: str) -> str | None:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = ast.literal_eval(node.value)
            return value if isinstance(value, str) else None
    return None


def main() -> int:
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
