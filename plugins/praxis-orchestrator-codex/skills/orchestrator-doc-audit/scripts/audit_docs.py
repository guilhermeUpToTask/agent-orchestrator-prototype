#!/usr/bin/env python3
"""Report likely stale claims in current (non-history) documentation."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
FILES = [ROOT / "README.md", ROOT / "CLAUDE.md", *sorted((ROOT / "docs" / "architecture").glob("*.md"))]
PATTERNS = {
    "legacy AGENT_MODE": re.compile(r"(?<!NO )\bAGENT_MODE\b"),
    "direct Redis coordination": re.compile(r"Redis.{0,50}(claim|queue|worker)", re.IGNORECASE),
    "stored navigation cursor": re.compile(r"stored (cursor|pointer)", re.IGNORECASE),
}


def main() -> int:
    findings = 0
    for path in FILES:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if any(negation in lowered for negation in (" no ", " not ", " never ", " gone ", " without ")):
                continue
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    print(f"{path.relative_to(ROOT)}:{number}: {label}: {line.strip()}")
                    findings += 1
    print(f"Documentation audit candidates: {findings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
