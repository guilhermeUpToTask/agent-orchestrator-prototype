---
description: "Split unstaged changes into atomic semantic commits with lint/type-check/test verification. Groups diffs by logical feature, validates each group independently, and commits in dependency order."
---

# /atomic-commits

Split the current working-tree diff into logically atomic, semantically meaningful git commits. Each commit must lint, type-check, and test-clean on its own.

## Trigger phrases

- "generate atomic commits"
- "split into atomic commits"
- "semantic git commits"
- "commit our changes atomically"

## Procedure

### Step 1 — Survey

```bash
git status --porcelain
git diff --stat
git log --oneline -5
```

Read the full diff. Identify the logical change-sets: group files by feature/fix/refactor/chore. Look for files that naturally belong together (same module, same feature, same concern).

### Step 2 — Plan commits

Write a numbered list of proposed commits, each with:
- **scope** — e.g. `feat(domain)`, `fix(api)`, `refactor(infra)`, `docs`, `test`
- **files** — which files belong to this commit
- **message** — conventional-commit-style message (1 line, imperative mood, explain *why*)
- **dependency** — which earlier commits this one depends on (if any)

Order by dependency: foundational changes first, dependents after.

### Step 3 — Stage and verify each commit

For each commit in order:

1. `git add` only the files for this commit
2. If the project has lint: run linter on staged-only tree
3. If the project has type-check: run type-checker on staged-only tree
4. If the project has unit tests: run relevant unit tests
5. If all pass: `git commit` with the planned message
6. If any fail: fix the issue, re-stage, re-verify, then commit

**Verification shortcut** (for Python projects with `ruff`/`mypy`/`pytest`):
```bash
git stash push --keep-index -u -q -m "verify-commit-N"
(ruff check src tests; mypy src; pytest -m "not integration" -q) 2>&1 | tail -20
git stash pop -q
```

### Step 4 — Final check

```bash
git status --short
git log --oneline -N  # where N = number of commits created
```

Confirm:
- Working tree is clean (all changes committed)
- Each commit is atomic and self-contained
- No commit breaks lint/type-check/tests

### Step 5 — Summary

Print a summary table:
```
# | scope | message | files
1 | feat(domain): ... | planner_orchestrator.py, task.py, ...
2 | feat(infra): ...  | container.py, openai_reasoner.py, ...
...
```

## Rules

- Never commit in a way that breaks lint, type-check, or tests at any intermediate step.
- Use `--no-ff` merge commits only when explicitly asked.
- Preserve existing commit message style from `git log --oneline -5`.
- If a single file has changes spanning multiple features, split it with `git add -p` (interactive patch staging).
- Do NOT push unless explicitly asked.
- Do NOT amend existing commits unless explicitly asked.
