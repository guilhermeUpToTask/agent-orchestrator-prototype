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

# Delete the previous run's branch hierarchy. Resetting `main` alone left every
# plan/cycle/goal/task branch in place, so run N started carrying ~3(N-1) stale
# branches — the runs stopped being comparable, which is the one thing a fixture
# must guarantee. This repo is disposable and re-materializable; the run's
# evidence lives in the captured run directory, not in these refs.
#
# Only these four prefixes are touched. `main` and the seed tag are never
# candidates: `git branch -D` on the checked-out branch fails anyway, and the
# prefix filter excludes it regardless.
# Prefix patterns, not `…/*`: a task branch is `task/<task_id>/a<attempt>`, two
# levels deep, and `refs/heads/task/*` silently misses it.
mapfile -t stale < <(git -C "$TARGET" for-each-ref --format='%(refname:short)' \
  refs/heads/plan refs/heads/cycle refs/heads/goal refs/heads/task)
if (( ${#stale[@]} )); then
  note "deleting ${#stale[@]} branch(es) from previous runs"
  git -C "$TARGET" branch -D "${stale[@]}" >/dev/null
else
  note "no previous-run branches to delete"
fi

note "HEAD=$(git -C "$TARGET" rev-parse --short HEAD) clean working tree"
note "start a new cycle/plan by POSTing the same brief.txt (see the fixture README)"
