#!/usr/bin/env bash
# Assert the happy-path success condition: greet("Ada") == "Hello, Ada!" via pytest.
set -Eeuo pipefail
IFS=$'\n\t'

ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
TARGET="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"
# Optional: check a specific worktree/branch checkout (e.g. plan or cycle branch path)
if [[ "${1:-}" != "" ]]; then
  TARGET="$1"
fi

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

[[ -d "$TARGET" ]] || die "missing checkout: $TARGET"
[[ -f "$TARGET/tests/test_greeter.py" ]] || die "not a happy-path seed layout: $TARGET"

if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required"
fi

note "running pytest in $TARGET"
cd "$TARGET"

if ! python3 -c "import pytest" 2>/dev/null; then
  note "pytest not importable; pip installing pytest for this check only"
  python3 -m pip install -q 'pytest>=8.0'
fi

# pythonpath=src is in pyproject; also export for bare pytest invocations
export PYTHONPATH="$TARGET/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest -q
note "SUCCESS: greet implementation satisfies the seed test"
