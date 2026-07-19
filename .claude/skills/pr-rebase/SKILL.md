---
name: pr-rebase
description: Use when a PR is blocked with "head is out of date", a stuck "Update branch" state, or strict up-to-date status checks after main moved. Rebases the PR branch onto its base and force-pushes via a single script - no manual git archaeology.
---

# PR rebase unstick

One tool script does the whole job. Given a PR number:

```bash
bash .claude/skills/pr-rebase/bin/rebase-pr.sh <pr-number> --wait
```

It fetches, rebases the PR's head branch onto its base in a **temp worktree**
(never touching the current working tree), force-pushes with lease, then with
`--wait` polls until GitHub reports `mergeable_state=clean` (ready to merge)
or `dirty` (real conflicts).

## Instructions

1. Run the script with the PR number the user gave (add `--wait` unless they
   asked for fire-and-forget). Do not reimplement its steps manually.
2. Report the outcome in one or two sentences:
   - exit 0 → "PR #N is ready to merge."
   - exit 2 → real merge conflicts; show `git log --oneline origin/<base>..origin/<branch>`
     and ask before resolving.
   - exit 3 → CI still running; check `gh pr checks <N>` once more before
     escalating.
3. Nothing else. No extra verification runs, no worktree cleanup (the script
   traps its own), no status essays.

Background: with strict required checks + linear history, GitHub's "Update
branch" button can wedge a PR (merge commit pushed, PR head not tracking it,
no CI on the new commit). Rebase + force-push resets head, CI, and the
up-to-date requirement in one move.
