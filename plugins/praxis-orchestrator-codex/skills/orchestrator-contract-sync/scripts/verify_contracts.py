#!/usr/bin/env python3
"""Regenerate API contracts twice, verify stability, and build the frontend."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
FRONTEND = ROOT / "frontend"
OUTPUTS = (
    FRONTEND / "openapi.json",
    FRONTEND / "src/types/generated/index.ts",
    FRONTEND / "src/types/generated/types.gen.ts",
)


def run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def hashes() -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in OUTPUTS}


def main() -> int:
    run("npm", "run", "generate:api", cwd=FRONTEND)
    first = hashes()
    run("npm", "run", "generate:api", cwd=FRONTEND)
    second = hashes()
    unstable = [str(path.relative_to(ROOT)) for path in OUTPUTS if first[path] != second[path]]
    if unstable:
        print("Nondeterministic generated files:")
        print("\n".join(f"- {path}" for path in unstable))
        return 1
    run("npm", "run", "build", cwd=FRONTEND)
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--", "frontend/openapi.json", "frontend/src/types/generated"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    print("Contract generation is deterministic.")
    print("Generated drift:" if diff else "Generated files match the index.")
    if diff:
        print(diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
