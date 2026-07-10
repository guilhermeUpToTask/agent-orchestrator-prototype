"""
scripts/export_openapi.py — Dump the FastAPI OpenAPI schema to a file.

Used by the frontend type generator (`npm run generate:api`) so types can
be regenerated without a running server:

    python scripts/export_openapi.py frontend/openapi.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.server import create_app  # noqa: E402


def _canonicalize(value: object, *, in_default: bool = False) -> object:
    """Make set-derived schema defaults deterministic across Python processes."""
    if isinstance(value, dict):
        return {
            item_key: _canonicalize(item, in_default=in_default or item_key == "default")
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        items = [_canonicalize(item, in_default=in_default) for item in value]
        if in_default and all(isinstance(item, (str, int, float, bool)) for item in items):
            return sorted(items, key=str)
        return items
    return value


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/openapi.json")
    schema = _canonicalize(create_app().openapi())
    out.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"OpenAPI schema written to {out}")


if __name__ == "__main__":
    main()
