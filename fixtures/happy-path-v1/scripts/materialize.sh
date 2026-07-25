#!/usr/bin/env bash
# Create (or re-seed) the disposable happy-path-v1 git repo outside the monorepo.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FIXTURE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SEED_DIR="$FIXTURE_DIR/seed"
SEED_TAG="happy-path-v1-seed"

ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
TARGET="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

command -v git >/dev/null 2>&1 || die "git is required"
[[ -d "$SEED_DIR" ]] || die "seed templates missing: $SEED_DIR"

if [[ -e "$TARGET" && ! -d "$TARGET/.git" ]]; then
  die "target exists but is not a git repo: $TARGET (move it aside or set HAPPY_PATH_REPO)"
fi

mkdir -p "$(dirname -- "$TARGET")"

if [[ ! -d "$TARGET/.git" ]]; then
  note "initializing git repo at $TARGET"
  mkdir -p "$TARGET"
  git -C "$TARGET" init -b main
  git -C "$TARGET" config user.email "happy-path-v1@localhost"
  git -C "$TARGET" config user.name "happy-path-v1"
else
  note "reusing existing repo at $TARGET"
fi

note "syncing seed files into $TARGET"
# Remove tracked content that is not part of the seed (keep .git).
find "$TARGET" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$SEED_DIR"/. "$TARGET"/

# Ensure pytest can run without a full editable install in simple checks.
if [[ ! -f "$TARGET/requirements-dev.txt" ]]; then
  printf 'pytest>=8.0\n' >"$TARGET/requirements-dev.txt"
fi

git -C "$TARGET" add -A
if git -C "$TARGET" diff --cached --quiet; then
  note "no file changes vs last commit"
else
  git -C "$TARGET" commit -m "happy-path-v1 seed: greet NotImplemented + failing test"
fi

git -C "$TARGET" tag -f "$SEED_TAG"
note "tagged $SEED_TAG at $(git -C "$TARGET" rev-parse --short HEAD)"
note "export PROJECT_REPO_DIR=$TARGET"
note "next: backend/scripts/dev.sh start --frontend  (then paste fixtures/happy-path-v1/BRIEF.md)"
