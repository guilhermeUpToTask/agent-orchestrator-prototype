---
description: "High-effort structured code review with recall-biased angle analysis. Produces a findings report with severity, location, and fix suggestions."
---

# /deep-review

Run a structured, high-recall code review on the specified scope (a directory, module, or set of files). Designed to catch every real bug a careful reviewer would find.

## Trigger phrases

- "deep review"
- "code review"
- "review this code"
- "find bugs in"
- "audit this module"

## Procedure

### Step 1 — Scope and baseline

Determine what to review:
- If a path is given: review that directory/module
- If no path: review all uncommitted changes (`git diff` + `git diff --cached`)
- If a commit range: review `git diff A..B`

Gather context:
```bash
# For directory/module review
find <scope> -name "*.py" -o -name "*.ts" -o -name "*.tsx" | head -50
wc -l $(find <scope> -name "*.py") | sort -rn | head -20

# For uncommitted changes
git diff --stat
git diff
```

### Step 2 — Angle analysis

For each file in scope, examine through these lenses (pick 3-5 most relevant):

| # | Angle | What to look for |
|---|-------|-----------------|
| 1 | **Correctness** | Logic errors, off-by-one, wrong comparisons, unreachable code |
| 2 | **Edge cases** | Null/empty/overflow, concurrent access, empty collections |
| 3 | **Error handling** | Swallowed exceptions, missing error paths, wrong error types |
| 4 | **Security** | Injection, path traversal, secrets in logs, unsafe deserialization |
| 5 | **Performance** | O(n²) loops, N+1 queries, memory leaks, unnecessary allocations |
| 6 | **Architecture** | Layer violations, circular deps, god objects, leaky abstractions |
| 7 | **Type safety** | Type narrowing failures, `any` types, unsafe casts |
| 8 | **API contract** | Breaking changes, missing validation, inconsistent error shapes |
| 9 | **Concurrency** | Race conditions, deadlocks, missing locks, thread safety |
| 10 | **Testing gaps** | Missing edge-case tests, untested error paths |

### Step 3 — Candidate generation

For each active angle, generate candidate findings (aim for 3-6 per angle). Each candidate is a specific, actionable observation with:
- **File and line** (exact location)
- **Severity** (critical / high / medium / low / nit)
- **Description** (what's wrong, why it matters)
- **Suggested fix** (concrete code change or approach)

### Step 4 — Verify and deduplicate

For each candidate:
1. Re-read the actual code to confirm the finding is real (not a false positive)
2. Check if it duplicates another finding
3. Assign final severity

Drop any finding that doesn't survive verification. Be honest — false positives erode trust.

### Step 5 — Report

Output a structured report:

```markdown
# Code Review Report

**Scope**: <what was reviewed>
**Files**: <N files, M lines>
**Date**: <date>

## Critical / High Findings

### [C1] <title>
- **File**: `path/to/file.py:42`
- **Severity**: critical
- **Description**: <what's wrong>
- **Impact**: <what could happen>
- **Fix**: <how to fix it>

### [C2] ...

## Medium Findings

### [M1] ...

## Low / Nit Findings

### [L1] ...

## Summary
- Critical: N
- High: N
- Medium: N
- Low: N
- Nit: N
```

## Rules

- **Recall over precision**: better to report a real finding with slightly uncertain severity than to miss a bug. But do verify before reporting.
- **No false positives**: every finding must be confirmed by re-reading the actual code.
- **Be specific**: "line 42 in task.py" not "somewhere in the task module".
- **Suggest fixes**: don't just point at problems — propose concrete solutions.
- **Respect architecture**: if the code intentionally violates a pattern for a documented reason, note it as a design decision, not a bug.
- **Scope discipline**: only review what was asked. Don't expand scope unless explicitly told to.
