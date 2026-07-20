#!/usr/bin/env bash
# Rebase a PR's head branch onto its base and force-push, unsticking the
# "head is out of date" / stuck update-branch state under strict status
# checks + linear history. Works in a temp worktree; never touches the
# current working tree.
#
# Usage: rebase-pr.sh <pr-number> [--wait]
#   --wait  poll until GitHub reports mergeable_state=clean (max ~3 min)
set -euo pipefail

pr="${1:?usage: rebase-pr.sh <pr-number> [--wait]}"
wait_flag="${2:-}"

repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
branch=$(gh api "repos/$repo/pulls/$pr" -q .head.ref)
base=$(gh api "repos/$repo/pulls/$pr" -q .base.ref)
state=$(gh api "repos/$repo/pulls/$pr" -q .state)
[ "$state" = "open" ] || { echo "PR #$pr is $state, nothing to do"; exit 1; }

echo "PR #$pr: $branch -> $base"
git fetch -q origin "$base" "$branch"

wt=$(mktemp -d /tmp/pr-rebase.XXXXXX)
cleanup() { git worktree remove --force "$wt" 2>/dev/null || true; }
trap cleanup EXIT

git worktree add -q --detach "$wt" "origin/$branch"
git -C "$wt" checkout -q -B "$branch" "origin/$branch"
# Rebase linearizes and drops any stuck "Update branch" merge commits.
git -C "$wt" rebase -q "origin/$base"
git -C "$wt" push --force-with-lease origin "$branch"
new_head=$(git -C "$wt" rev-parse HEAD)
echo "pushed rebased head $new_head; CI rerunning"

if [ "$wait_flag" = "--wait" ]; then
  for _ in $(seq 1 18); do
    sleep 10
    ms=$(gh api "repos/$repo/pulls/$pr" -q .mergeable_state)
    echo "mergeable_state: $ms"
    if [ "$ms" = "clean" ]; then echo "PR #$pr is ready to merge"; exit 0; fi
    if [ "$ms" = "dirty" ]; then echo "PR #$pr has real conflicts - needs manual resolution"; exit 2; fi
  done
  echo "timed out waiting; check 'gh pr checks $pr' - CI may still be running"
  exit 3
fi
