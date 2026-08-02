#!/usr/bin/env bash
# Create (or re-seed) the disposable static-site-v1 repo OUTSIDE this monorepo.
#
# A demo runs once and is captured, but materializing is still repeatable so a
# red run can be re-attempted from an identical starting point when the cause
# was the harness rather than the orchestrator.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEMO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SEED_DIR="$DEMO_DIR/seed"
SEED_TAG="static-site-v1-seed"

ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
TARGET="${STATIC_SITE_REPO:-$ORCH_HOME/demos/static-site-v1/repo}"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

command -v git >/dev/null 2>&1 || die "git is required"
[[ -d "$SEED_DIR" ]] || die "seed templates missing: $SEED_DIR"

if [[ -e "$TARGET" && ! -d "$TARGET/.git" ]]; then
  die "target exists but is not a git repo: $TARGET (move it aside or set STATIC_SITE_REPO)"
fi

mkdir -p "$(dirname -- "$TARGET")"

if [[ ! -d "$TARGET/.git" ]]; then
  note "initializing git repo at $TARGET"
  mkdir -p "$TARGET"
  git -C "$TARGET" init -b main
  git -C "$TARGET" config user.email "static-site-v1@localhost"
  git -C "$TARGET" config user.name "static-site-v1"
else
  note "reusing existing repo at $TARGET"
  # Any leftover cycle/goal/task branches make two runs incomparable. Matching
  # by ref PREFIX because a task branch is task/<id>/<run> and refs/heads/task/*
  # silently misses the deeper level — the trap happy-path-v1 hit.
  while read -r ref; do
    [[ -n "$ref" ]] && git -C "$TARGET" branch -D "$ref" >/dev/null 2>&1 || true
  done < <(git -C "$TARGET" for-each-ref --format='%(refname:short)' \
             refs/heads/cycle refs/heads/goal refs/heads/task \
             'refs/heads/cycle/**' 'refs/heads/goal/**' 'refs/heads/task/**' 2>/dev/null || true)
  git -C "$TARGET" worktree prune >/dev/null 2>&1 || true
  git -C "$TARGET" checkout -q main 2>/dev/null || true
fi

note "syncing seed files into $TARGET"
find "$TARGET" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$SEED_DIR"/. "$TARGET"/

if [[ ! -f "$TARGET/requirements-dev.txt" ]]; then
  printf 'pytest>=8.0\n' >"$TARGET/requirements-dev.txt"
fi

git -C "$TARGET" add -A
if git -C "$TARGET" diff --cached --quiet; then
  note "no file changes vs last commit"
else
  git -C "$TARGET" commit -q -m "static-site-v1 seed: empty generator + content to render"
fi

# The tag is what `verify_demo.py --seed-tag` compares the default branch
# against, which is how "plan work never touches the default branch" is checked
# rather than assumed.
git -C "$TARGET" tag -f "$SEED_TAG" >/dev/null
note "tagged $SEED_TAG at $(git -C "$TARGET" rev-parse --short HEAD)"

cat <<EOF

Repository ready:  $TARGET
Seed tag:          $SEED_TAG

Bind a project to it with repo_url set to that PATH — a project with no
repo_url silently gets a scratch repository somewhere else, and the run would
"pass" against a tree nobody looked at.

  export STATIC_SITE_REPO="$TARGET"
EOF
