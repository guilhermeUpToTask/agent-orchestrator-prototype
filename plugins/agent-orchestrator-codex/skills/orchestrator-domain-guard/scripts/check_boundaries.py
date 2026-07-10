#!/usr/bin/env python3
"""Enforce the repository's domain/app import boundaries."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SRC = ROOT / "backend" / "src"


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.lineno, node.module))
    return modules


def main() -> int:
    failures: list[str] = []
    policies = (
        (SRC / "domain", ("src.app", "src.infra", "src.api")),
        (SRC / "app", ("src.infra",)),
    )
    for directory, forbidden in policies:
        for path in directory.rglob("*.py"):
            for line, module in imported_modules(path):
                if module.startswith(forbidden):
                    failures.append(f"{path.relative_to(ROOT)}:{line}: forbidden import {module}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Architecture import boundaries: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
