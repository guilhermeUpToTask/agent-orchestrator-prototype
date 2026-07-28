"""The Phase 3 audit must not silently rot.

`docs/architecture/capability-matrix.md` states which operator workflows this
system supports and where each capability is exposed. An audit is only worth
something while it is true, and the failure mode is not a wrong row — it is a
route that appears months later and is never classified, so nobody notices that
the matrix stopped describing the product.

So the route inventory is checked both ways: every operation the app serves is
named in the matrix, and every operation the matrix names is really served. The
frontend and tests columns cannot be machine-checked; they are re-audited when a
phase closes.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.api.server import create_app

MATRIX = (
    Path(__file__).resolve().parents[3] / "docs" / "architecture" / "capability-matrix.md"
)

_METHODS = {"GET", "POST", "PUT", "DELETE"}
# Operations are written as `METHOD /path` inside backticks, so prose can name a
# path without being mistaken for a matrix entry.
_OPERATION = re.compile(r"`(GET|POST|PUT|DELETE) (/[A-Za-z0-9/{}_.-]*)`")


def _served() -> set[str]:
    paths = create_app().openapi()["paths"]
    return {
        f"{method.upper()} {path}"
        for path, operations in paths.items()
        for method in operations
        if method.upper() in _METHODS
    }


def _documented() -> set[str]:
    return {f"{method} {path}" for method, path in _OPERATION.findall(MATRIX.read_text())}


def test_every_served_operation_is_classified() -> None:
    missing = sorted(_served() - _documented())

    assert missing == [], (
        "these operations are served but absent from the capability matrix — add "
        "a row with its frontend consumer, tests, status and launch priority: "
        f"{missing}"
    )


def test_the_matrix_names_no_operation_that_does_not_exist() -> None:
    phantom = sorted(_documented() - _served())

    assert phantom == [], (
        "the capability matrix documents operations the app does not serve; a "
        f"removed or renamed route needs its row updated: {phantom}"
    )
