#!/usr/bin/env python3
"""Validate the repository plugin without external dependencies."""
from __future__ import annotations

import compileall
import json
import re
import subprocess
import sys
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError(f"{path}: unterminated frontmatter") from error
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key] = value
    return values


def run(script: Path, *args: str) -> None:
    subprocess.run([sys.executable, str(script), *args], cwd=REPO, check=True)


def main() -> int:
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest["name"] != PLUGIN.name:
        raise ValueError("plugin name must match its directory")
    marketplace = json.loads((REPO / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    entry = entries.get(manifest["name"])
    if not entry or entry["source"]["path"] != f"./plugins/{PLUGIN.name}":
        raise ValueError("marketplace entry is missing or points at the wrong plugin")
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    agents = sorted((PLUGIN / "agents").glob("*.md"))
    if len(skills) != 7 or len(agents) != 4:
        raise ValueError(f"expected 7 skills and 4 agents, found {len(skills)} and {len(agents)}")
    for path in skills:
        meta = frontmatter(path)
        if meta.get("name") != path.parent.name or not NAME.fullmatch(meta["name"]):
            raise ValueError(f"{path}: invalid or mismatched name")
        if len(meta.get("description", "")) < 80:
            raise ValueError(f"{path}: description is not trigger-specific")
        ui = path.parent / "agents/openai.yaml"
        content = ui.read_text(encoding="utf-8")
        if f"${meta['name']}" not in content:
            raise ValueError(f"{ui}: default prompt must mention the skill")
    for path in agents:
        meta = frontmatter(path)
        if meta.get("name") != path.stem or not meta.get("description"):
            raise ValueError(f"{path}: invalid agent metadata")
    if not compileall.compile_dir(PLUGIN, quiet=1):
        raise ValueError("Python compilation failed")
    run(PLUGIN / "skills/orchestrator-domain-guard/scripts/check_boundaries.py")
    run(PLUGIN / "skills/orchestrator-migration/scripts/check_migration_chain.py")
    run(PLUGIN / "skills/orchestrator-runtime-adapter/scripts/runtime_surface.py")
    run(PLUGIN / "skills/orchestrator-doc-audit/scripts/audit_docs.py")
    run(
        PLUGIN / "skills/orchestrator-change-impact/scripts/change_impact.py",
        "backend/src/api/routers/plans.py",
        "frontend/src/lib/api.ts",
        "--json",
    )
    print("Agent Orchestrator Codex plugin: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
