#!/usr/bin/env bash
# Hard-reset the disposable happy-path repo to the seed tag. Safe between runs.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SEED_TAG="happy-path-v2-seed"
# Mirrors praxis_orchestrator/infra/env_compat.py: an explicit home wins, a
# fresh install is ~/.praxis, and a pre-rename ~/.orchestrator is adopted in
# place rather than left behind holding the operator's only database.
ORCH_HOME="${PRAXIS_HOME:-${ORCHESTRATOR_HOME:-$HOME/.praxis}}"
[ -d "$ORCH_HOME" ] || [ ! -d "$HOME/.orchestrator" ] || ORCH_HOME="$HOME/.orchestrator"
TARGET="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v2/repo}"

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

# Delete the fixture's PLAN state, not just its worktree. Resetting the repo
# alone left the long-lived plan in place, so every re-run stacked another cycle
# onto it and no two runs started from the same state. A project owns exactly one
# plan (ADR-003), so the plan must go for the next run to be a fresh one.
#
# Best-effort by design: reset must still work with the API down (the git reset
# above is the part that always applies). Only plans bound to THIS fixture's
# project are touched — never anything else in the state directory.
if [[ "${HAPPY_PATH_SKIP_PLAN_RESET:-}" == "1" ]]; then
  note "plan reset skipped (HAPPY_PATH_SKIP_PLAN_RESET=1)"
elif ! command -v jq >/dev/null 2>&1; then
  note "warning: jq not found; plan state NOT reset (git reset still applied)"
elif ! "$SCRIPT_DIR/api.sh" GET /api/projects >/dev/null 2>&1; then
  note "warning: API unreachable; plan state NOT reset (git reset still applied)"
  note "         start the API and re-run, or the next run continues the old plan"
else
  project_ids="$("$SCRIPT_DIR/api.sh" GET /api/projects \
    | jq -r --arg repo "$TARGET" '.[] | select(.repo_url == $repo) | .id')"
  if [[ -z "$project_ids" ]]; then
    note "no project bound to $TARGET — no plan state to reset"
  else
    removed=0
    for project_id in $project_ids; do
      plan_ids="$("$SCRIPT_DIR/api.sh" GET /api/plans \
        | jq -r --arg p "$project_id" '.[] | select(.project_id == $p) | .id')"
      for plan_id in $plan_ids; do
        if "$SCRIPT_DIR/api.sh" DELETE "/api/plans/$plan_id" >/dev/null 2>&1; then
          removed=$((removed + 1))
        else
          # 409 PLAN_BUSY: a worker still holds the lease. Say so — silently
          # leaving the plan is how the next run inherits the old one.
          note "warning: could not delete plan $plan_id (still claimed? stop the worker)"
        fi
      done
    done
    note "deleted $removed plan(s) for this fixture; cycles/attempts/evidence went with them"
  fi
fi

note "HEAD=$(git -C "$TARGET" rev-parse --short HEAD) clean working tree"
note "start a new cycle/plan by POSTing the same brief.txt (see the fixture README)"
