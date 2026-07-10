#!/usr/bin/env python3
"""Select focused verification commands from changed paths."""
from __future__ import annotations

import argparse
import subprocess


RULES = (
    (("backend/src/domain/", "backend/src/app/"), "cd backend && pytest -q tests/unit/orchestration"),
    (("backend/src/infra/db/",), "cd backend && pytest -q tests/integration/test_reference_repos.py"),
    (("backend/alembic/", "backend/src/infra/db/tables.py"), "cd backend && pytest -q tests/integration/test_migrations.py"),
    (("backend/src/api/",), "cd backend && pytest -q tests/integration/test_api.py"),
    (("backend/src/infra/git/",), "cd backend && pytest -q tests/integration/test_git_workspace.py tests/integration/test_drive_plan_sqlite_git.py"),
    (("backend/src/infra/runtime/",), "cd backend && pytest -q tests/integration/test_runner_taxonomy.py tests/integration/test_agent_runner_factory.py"),
    (("backend/src/infra/reasoner/",), "cd backend && pytest -q tests/unit/reasoner tests/integration/test_full_cycle_llm.py"),
    (("frontend/", "backend/src/api/"), "cd frontend && npm run build"),
    (("frontend/", "backend/src/api/"), "cd frontend && npm run generate:api"),
)


def paths(explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    found: set[str] = set()
    for command in (["git", "diff", "--name-only", "--cached"], ["git", "diff", "--name-only"]):
        found.update(subprocess.run(command, check=True, text=True, capture_output=True).stdout.splitlines())
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    changed = paths(args.paths)
    commands = {command for prefixes, command in RULES if any(path.startswith(prefixes) for path in changed)}
    if any(path.startswith("backend/") for path in changed):
        commands.add("cd backend && make check")
    print("Changed paths:")
    print("\n".join(f"- {path}" for path in changed) or "- none")
    print("\nVerification commands:")
    print("\n".join(f"- {command}" for command in sorted(commands)) or "- no mapped command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
