#!/usr/bin/env bash
# Build the UI and stage it inside the Python package, so `uv build` ships one
# artifact containing the CLI, the API, the worker and the frontend.
#
#   ./backend/scripts/build_frontend.sh
#   ./backend/scripts/build_frontend.sh --check   # verify staged output is current
#
# Run before `uv build`. The release workflow calls it; a source checkout does
# not need it at all — the API serves the bundle only when it is present.
#
# The staged directory is generated, never edited by hand, and is git-ignored:
# committing a build output would make the tree disagree with the sources it
# was built from the moment either changed.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO_DIR="$(cd -- "$BACKEND_DIR/.." && pwd -P)"
FRONTEND_DIR="$REPO_DIR/frontend"
STAGED="$BACKEND_DIR/agent_orchestrator/api/static"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

[[ -d "$FRONTEND_DIR" ]] || die "frontend/ not found at $FRONTEND_DIR"
command -v npm >/dev/null 2>&1 || die "npm is required to build the UI"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  [[ -f "$STAGED/index.html" ]] || die "no staged bundle — run $0 first"
  note "staged bundle present: $STAGED"
  exit 0
fi

note "building the frontend"
(cd "$FRONTEND_DIR" && npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null)
(cd "$FRONTEND_DIR" && npm run build)

[[ -f "$FRONTEND_DIR/dist/index.html" ]] || die "npm run build produced no dist/index.html"

note "staging into $STAGED"
rm -rf "$STAGED"
mkdir -p "$STAGED"
cp -a "$FRONTEND_DIR/dist/." "$STAGED/"

note "staged $(find "$STAGED" -type f | wc -l) files"
note "next: (cd backend && uv build)"
