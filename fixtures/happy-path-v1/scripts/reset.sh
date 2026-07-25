#!/usr/bin/env bash
# Hard-reset the disposable happy-path repo to the seed tag. Safe between runs.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SEED_TAG="happy-path-v1-seed"
ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
TARGET="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

[[ -d "$TARGET/.git" ]] || die "no repo at $TARGET — run materialize.sh first"
git -C "$TARGET" rev-parse "$SEED_TAG" >/dev/null 2>&1 \
  || die "missing tag $SEED_TAG — run materialize.sh"

note "resetting $TARGET to $SEED_TAG"
# Drop local worktree dirt from agent attempts that leaked into the main checkout.
git -C "$TARGET" checkout main >/dev/null 2>&1 || git -C "$TARGET" checkout -B main "$SEED_TAG"
git -C "$TARGET" reset --hard "$SEED_TAG"
git -C "$TARGET" clean -fdx

# Best-effort: prune worktrees left by crashed workers (never fails the reset).
git -C "$TARGET" worktree prune 2>/dev/null || true

note "HEAD=$(git -C "$TARGET" rev-parse --short HEAD) clean working tree"
note "start a new cycle/plan with the same BRIEF.md"
