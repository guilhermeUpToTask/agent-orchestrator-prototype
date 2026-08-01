"""Serving the packaged UI from the API that owns its types.

Phase 6 ships ONE artifact. The built frontend lives inside the package at
`agent_orchestrator/api/static/`, populated at build time from `frontend/dist` (see
`scripts/build_frontend.sh`), so `pip install` gives an operator the CLI, the
API, the worker and the UI together — and the bundle can never disagree with
the API version it was generated against, because they are the same install.

Serving it here also removes a second port and a CORS negotiation from the
first-run path, which is where a new operator is least able to tell a setup
mistake from a broken product.

The bundle is OPTIONAL by design. A source checkout that has never run
`npm run build` starts exactly as before — that is how the whole test suite and
every fixture run operate, and making the API refuse to start without a UI it
does not need would be a strange way to ship a backend.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


#: Paths the API owns. The SPA fallback must refuse these rather than answer
#: them with the HTML shell — `/docs` and `/openapi.json` included, because a
#: generator pointed at a 200-OK HTML page fails in a far more confusing way
#: than one pointed at a 404.
_RESERVED_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")


def bundle_dir() -> Path:
    """Derived from THIS module's location, never from a repository marker —
    the same property the migrations needed (`infra/db/migration_config.py`)."""
    return Path(__file__).resolve().parent / "static"


def mount_frontend(app: FastAPI) -> bool:
    """Mount the built UI if it shipped. Returns whether anything was mounted.

    Called AFTER every API route is registered: `/api/...` and `/health` are
    matched first, and only an unmatched path reaches the SPA fallback. An
    unknown `/api` path therefore still 404s as JSON the client can interpret,
    rather than being answered with an HTML page — a fallback that swallows the
    API is worse than no fallback at all.
    """
    directory = bundle_dir()
    index = directory / "index.html"
    if not index.is_file():
        return False

    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str) -> FileResponse:
        """Client-side routes (`/plans/<id>/goals`) exist only in the browser
        router, so a deep link or a refresh must resolve to the shell rather
        than 404. Files that really do ship (favicon, manifest) are served from
        disk first."""
        # Registering last is not enough on its own: an unmatched `/api/...`
        # path still reaches this catch-all, and answering it with the HTML
        # shell would turn every client's 404 handling into a parse error.
        # Caught by `test_the_api_still_wins_over_the_fallback`.
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (directory / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(directory.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    return True
