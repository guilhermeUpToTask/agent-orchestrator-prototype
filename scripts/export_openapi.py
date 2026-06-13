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


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/openapi.json")
    schema = create_app().openapi()
    out.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"OpenAPI schema written to {out}")


if __name__ == "__main__":
    main()
