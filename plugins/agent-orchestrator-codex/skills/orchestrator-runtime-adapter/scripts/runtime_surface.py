#!/usr/bin/env python3
"""Report required runtime extension anchors and whether they exist."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
ANCHORS = (
    "backend/src/domain/ports/agent_port.py",
    "backend/src/domain/ports/reasoner_port.py",
    "backend/src/infra/runtime/cli_runner.py",
    "backend/src/infra/runtime/factory.py",
    "backend/src/infra/runtime/taxonomy.py",
    "backend/src/infra/runtime/dependency_checker.py",
    "backend/src/infra/reasoner/factory.py",
    "backend/src/infra/reasoner/openai_reasoner.py",
    "backend/src/infra/db/secret_store.py",
    "backend/src/infra/container.py",
    "backend/tests/integration/test_runner_taxonomy.py",
    "backend/tests/integration/test_agent_runner_factory.py",
    "backend/tests/integration/test_reasoner_factory.py",
    "backend/tests/unit/reasoner",
)


def main() -> int:
    missing = []
    for anchor in ANCHORS:
        exists = (ROOT / anchor).exists()
        print(f"{'OK' if exists else 'MISSING':7} {anchor}")
        if not exists:
            missing.append(anchor)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
