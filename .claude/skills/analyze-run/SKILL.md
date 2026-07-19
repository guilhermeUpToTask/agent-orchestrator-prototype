---
name: analyze-run
description: Produce an evidence-based analysis report for one .orchestrator/runtime-runs/ acceleration experiment — routing/escalation timeline, runtime comparison, and an independent code review of what actually merged. Use after an /accelerate run to turn its raw JSONL evidence into a report for the human.
argument-hint: "<experiment_id> [base_commit..head_commit]"
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash, Write
---

# Analyze a runtime-acceleration run

Turn `.orchestrator/runtime-runs/$1/events.jsonl` plus the actual merged diff into a report.
This is analysis and writing, not implementation — never edit source files from this skill.

## 1. Pull the evidence (mechanical — use the scripts, don't hand-parse JSONL)

```
python3 .orchestrator/lib/timeline.py <experiment_id>   # per-task chronological narrative
python3 .orchestrator/lib/report.py <experiment_id>      # per-runtime comparison table
```

If `report.md`/`report.json` don't exist yet under `.orchestrator/runtime-runs/<experiment_id>/`,
the second command generates them. Both are pure aggregation — trust their numbers, don't
recompute by hand.

## 2. Independently review the code — do not summarize the implementing agent's own report

The implementing agent's final message is a *claim*, not evidence. For every task in the
timeline:

- Diff the actual merged range (`git diff <base>..<head> -- <touched paths>` — get the range
  from the task's `git_change` events or the merge commits themselves).
- Read the changed files in full, not just the diff hunks, enough to judge correctness.
- Actively look for: behavior that contradicts what the task prompt asked for; stale
  docs/docstrings; unbounded resource use; sync/async or threading hazards; security
  regressions (auth/ownership checks, secret handling); anything that only *looks* done — a
  detailed, confident summary is not evidence a tool call actually ran. Re-run the verification
  commands yourself in a real worktree if the merged commit's evidence doesn't already show it
  (`make check` or the project's equivalent) — never take "verification passed" from the agent's
  prose alone.
- Grade findings: a positive callout for something a runtime genuinely improved (e.g. a bug the
  second attempt caught), moderate/low for real but non-blocking gaps. Don't manufacture
  findings to pad the report — an honest "nothing further found" section is fine.

## 3. Write the report

Use `.claude/skills/analyze-run/assets/report-template.html` as the starting structure and
design system (color tokens, type stack, timeline/table/finding-card components already
defined for both light and dark themes) — copy it, don't reinvent the CSS. Replace every
section with this run's real content:

- **Header** — experiment id, branch, task/suite counts, time window.
- **Executive summary** — 2-4 sentences plus a 4-stat row (tasks verified, escalations, first-try
  success rate, tests added/regressed).
- **Task timeline** — one entry per task from step 1's output; show every attempt (runtime,
  outcome, duration) and the escalation reason when a task needed more than one runtime.
- **Runtime comparison** — the table from `report.py`, verbatim numbers; keep the "ranked by
  verified completions, not tokens" framing and the honest `—`/unavailable convention.
- **Code review findings** — from step 2, most significant first, file:line anchors.
- **Recommendations** — concrete, file-anchored, ranked by what to fix first.
- **Evidence trail footer** — paths to the `events.jsonl`/`report.md` and the merge commit(s).

Publish with the Artifact tool (favicon `📡` for continuity with prior reports on this project,
unless the user asks for something else). Keep the design honest to the data — no invented
numbers, no smoothing over a runtime's real failures.

## Notes

- This skill only reads `.orchestrator/` and the repo's git history — it never touches
  `runtime-pool.yaml` or `.claude/skills/accelerate/`. If the analysis surfaces a routing lesson
  (a runtime that fabricated success, a model override that broke tool use, a wrapper bug), say
  so in the report and suggest the manifest edit — don't make it from here.
- Keep this skill light: it delegates all heavy lifting to the two existing scripts and to
  reading real code. Don't grow a third script unless the two aggregations genuinely can't
  express something the report needs.
