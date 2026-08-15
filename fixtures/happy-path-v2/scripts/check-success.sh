#!/usr/bin/env bash
# The fixture's verdict on a checkout — expectation 7.
#
#   ./scripts/check-success.sh [/path/to/checkout]
#
# v1 ran `pytest` inside the repo, in the same tests/ the agent writes to, so a
# weak test the agent wrote and then implemented to would pass and the run would
# be called green. The verdict was circular.
#
# Here the acceptance suite lives OUTSIDE the disposable repo, in the fixture
# directory. It is copied into the checkout, run, and removed — never committed,
# never visible to an agent, never satisfiable by construction. It asserts the
# behaviour, that a check was authored at all, and that the check actually
# DISCRIMINATES (a mutation probe against a deliberately broken greet).
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FIXTURE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ACCEPTANCE="$FIXTURE_DIR/acceptance"

ORCH_HOME="${PRAXIS_HOME:-$HOME/.praxis}"
TARGET="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v2/repo}"
if [[ "${1:-}" != "" ]]; then
  TARGET="$1"
fi

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

[[ -d "$TARGET" ]] || die "missing checkout: $TARGET"
[[ -f "$TARGET/src/happy_path/greeter.py" ]] || die "not a happy-path-v2 layout: $TARGET"
[[ -d "$ACCEPTANCE" ]] || die "acceptance suite missing: $ACCEPTANCE"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

if ! python3 -c "import pytest" 2>/dev/null; then
  note "pytest not importable; installing for this check only"
  python3 -m pip install -q 'pytest>=8.0'
fi

# Copy in, run, remove — on failure too, so a red run never leaves the
# acceptance suite behind where a later agent could read it.
STAGED="$TARGET/.acceptance"
cleanup() { rm -rf "$STAGED"; }
trap cleanup EXIT

rm -rf "$STAGED"
cp -a "$ACCEPTANCE" "$STAGED"

note "running acceptance against $TARGET"
cd "$TARGET"
export PYTHONPATH="$TARGET/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest -q .acceptance

note "SUCCESS: greet is correct, a check was authored, and it discriminates"
