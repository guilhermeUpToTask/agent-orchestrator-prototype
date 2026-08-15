#!/usr/bin/env bash
# Create (or re-seed) the disposable first-cycle-v1 git repo outside the monorepo.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FIXTURE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SEED_DIR="$FIXTURE_DIR/seed"
SEED_TAG="first-cycle-v1-seed"

# Mirrors praxis_orchestrator/infra/env_compat.py: an explicit home wins, a
# fresh install is ~/.praxis, and a pre-rename ~/.orchestrator is adopted in
# place rather than left behind holding the operator's only database.
ORCH_HOME="${PRAXIS_HOME:-${ORCHESTRATOR_HOME:-$HOME/.praxis}}"
[ -d "$ORCH_HOME" ] || [ ! -d "$HOME/.orchestrator" ] || ORCH_HOME="$HOME/.orchestrator"
TARGET="${FIRST_CYCLE_REPO:-$ORCH_HOME/first-cycle-v1/repo}"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

command -v git >/dev/null 2>&1 || die "git is required"
[[ -d "$SEED_DIR" ]] || die "seed templates missing: $SEED_DIR"

if [[ -e "$TARGET" && ! -d "$TARGET/.git" ]]; then
  die "target exists but is not a git repo: $TARGET (move it aside or set FIRST_CYCLE_REPO)"
fi

mkdir -p "$(dirname -- "$TARGET")"

if [[ ! -d "$TARGET/.git" ]]; then
  note "initializing git repo at $TARGET"
  mkdir -p "$TARGET"
  git -C "$TARGET" init -b main
  git -C "$TARGET" config user.email "first-cycle-v1@localhost"
  git -C "$TARGET" config user.name "first-cycle-v1"
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
  git -C "$TARGET" commit -m "first-cycle-v1 seed: slugify NotImplemented + empty tests/"
fi

git -C "$TARGET" tag -f "$SEED_TAG"
note "tagged $SEED_TAG at $(git -C "$TARGET" rev-parse --short HEAD)"
# NOT PROJECT_REPO_DIR: AppContainer does not read it here. A project created
# without `repo_url` gets a fresh empty repo auto-seeded under PRAXIS_HOME,
# and the run then "passes" against a tree the checker never looks at.
note "bind this repo by creating the project WITH repo_url:"
note "  POST /api/projects {\"name\":\"first-cycle-v1\",\"repo_url\":\"$TARGET\"}"
note "next: backend/scripts/dev.sh start   (API + worker; the walkthrough is API-only)"
note "then: POST fixtures/first-cycle-v1/brief.txt as a plan -- see the fixture README"
