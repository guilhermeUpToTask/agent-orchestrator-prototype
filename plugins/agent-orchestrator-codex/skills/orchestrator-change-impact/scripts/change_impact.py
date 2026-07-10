#!/usr/bin/env python3
"""Map changed paths to Agent Orchestrator impact obligations."""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    prefixes: tuple[str, ...]
    layer: str
    risks: tuple[str, ...]
    artifacts: tuple[str, ...]
    tests: tuple[str, ...]
    docs: tuple[str, ...] = ()


RULES = (
    Rule(("backend/src/domain/",), "domain", ("frozen domain", "aggregate authority", "phase machine"), (), ("backend/tests/unit/orchestration",), ("docs/decisions/decision-log.md",)),
    Rule(("backend/src/app/",), "application", ("CAS", "transactional outbox", "side-effect boundary"), (), ("backend/tests/unit/orchestration",)),
    Rule(("backend/src/infra/db/",), "database adapter", ("fake/SQLite parity", "transactionality"), (), ("backend/tests/integration/test_reference_repos.py", "backend/tests/unit/orchestration")),
    Rule(("backend/alembic/",), "migration", ("upgrade compatibility", "data preservation"), (), ("backend/tests/integration/test_migrations.py",)),
    Rule(("backend/src/api/",), "API", ("central error mapping", "outbox-to-SSE path"), ("frontend/openapi.json", "frontend/src/types/generated/"), ("backend/tests/integration/test_api.py",), ("docs/architecture/",)),
    Rule(("backend/src/infra/runtime/",), "agent runtime", ("shared failure taxonomy", "catalog bindings", "secret boundary"), (), ("backend/tests/integration/test_runner_taxonomy.py", "backend/tests/integration/test_agent_runner_factory.py")),
    Rule(("backend/src/infra/reasoner/",), "reasoner", ("two-method port", "provider self-correction", "telemetry"), (), ("backend/tests/unit/reasoner", "backend/tests/integration/test_full_cycle_llm.py")),
    Rule(("frontend/",), "frontend", ("generated/handwritten type split", "SSE dedup"), (), ("cd frontend && npm run build",)),
    Rule(("docs/", "README.md", "CLAUDE.md", "ROADMAP.md"), "documentation", ("current vs planned vs history",), (), (), ()),
    Rule((".github/", "release-please-config.json"), "delivery", ("required check names", "minimal permissions"), (), ("workflow validation",)),
)


def changed_paths(explicit: list[str]) -> list[str]:
    if explicit:
        return sorted(set(explicit))
    commands = (
        ["git", "diff", "--name-only", "--cached"],
        ["git", "diff", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    paths: set[str] = set()
    for command in commands:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
        paths.update(line for line in result.stdout.splitlines() if line)
    return sorted(paths)


def classify(paths: list[str]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {key: set() for key in ("layers", "risks", "artifacts", "tests", "docs")}
    for path in paths:
        for rule in RULES:
            if path.startswith(rule.prefixes):
                result["layers"].add(rule.layer)
                result["risks"].update(rule.risks)
                result["artifacts"].update(rule.artifacts)
                result["tests"].update(rule.tests)
                result["docs"].update(rule.docs)
    if {"API", "frontend"} & result["layers"]:
        result["tests"].update(("cd frontend && npm run generate:api", "cd frontend && npm run build"))
    if result["layers"] - {"documentation", "delivery"}:
        result["tests"].add("cd backend && make check")
    return {key: sorted(values) for key, values in result.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = changed_paths(args.paths)
    manifest = {"paths": paths, **classify(paths)}
    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        for heading, values in manifest.items():
            print(f"## {heading.replace('_', ' ').title()}")
            print("\n".join(f"- {value}" for value in values) if values else "- none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
