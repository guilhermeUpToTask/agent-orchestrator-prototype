# Prompt template — autonomous roadmap execution (/loop + /accelerate)

Paste the block below after `/loop` (fill the [brackets]). It reproduces the
2026-07-19 session's working pattern: self-paced loop, multi-runtime accelerate
waves, evidence-first verification, and a consolidated report at the end.

---

## The prompt

```
/loop Work through ROADMAP.md using the /accelerate protocol and the runtime
pool, starting from [SCOPE: e.g. "the Now sections", "items N-M", "the live
plan walkthrough"]. Be wise with consumption across all agents; when usage
nears its limit or the scope completes, stop and generate a full report.

Policies for this run:
- PR gating: [pick one]
  (a) one PR per verified task, wait for my merge between waves, or
  (b) integrate fixes on a local branch [BRANCH, e.g. work/accelerate-YYYY-MM-DD],
      single consolidated PR at the end.
- Issues: [file GitHub issues autonomously with evidence | collect findings
  in the report only].
- Human gates: PR merges and repo-settings changes are mine; anything
  requiring a domain un-freeze or architecture change stops and asks me.
- If running the live system: use dev.sh start in the background, watch the
  SSE feed with a persistent Monitor, restart only at plan-safe boundaries,
  and never bypass the aggregate's guarded transitions (ask before any direct
  DB repair).
```

## What the coordinator must remember (lessons already paid for)

1. **Follow the accelerate protocol exactly**: preconditions (insights rollup,
   shared-abstractions grep, git state, probes for any runtime you'll route),
   concern-signature wave planning, one worktree per writer, wrapper-logged
   evidence for every routing decision and outcome, ledger + report + insights
   regeneration at the end.
2. **Absolute paths in every wrapper/CLI invocation** — three dispatches
   failed in the reference session because shell cwd persisted between Bash
   calls and relative `--cd`/`--cwd`/wrapper paths broke. Never `cd` in setup
   commands you'll follow with a dispatch.
3. **Verify externally, in the task's own worktree, with its own venv** —
   never trust agent prose; ruff + mypy + full pytest before any integration.
   Expect to repair delegated work (deadlocking tests, over-specified
   assertions, positional flakes) — repair as coordinator, don't redispatch
   for style.
4. **Routing defaults that worked**: codex for implementation/refactor/debug
   (7/7 verified), grok for mechanical/docs, claude_sonnet for read-only recon
   only; mimo/pi_free stay off unless insights.md says otherwise.
5. **Live-walkthrough loop shape**: block/failure event → pull attempt log →
   classify (orchestrator defect vs agent/model flakiness vs plan content) →
   file issue with evidence → dispatch fix in parallel → integrate → restart
   at a safe boundary → retry via the block's legal resolution → confirm the
   fix live before moving on. Expect layered bugs: each fix exposes the next.
6. **Loop mechanics**: ScheduleWakeup 1200–1800s is only the fallback
   heartbeat — task-notifications and the Monitor are the real wake signals.
   Stop = final report file + PushNotification + ScheduleWakeup stop + stop
   the Monitor and any dev.sh supervisor you started.
7. **Report contents**: findings table (defect → fix → live proof), per-runtime
   routed/verified stats, consumption note, what remains and why you stopped,
   evidence paths (`.orchestrator/runtime-runs/<experiment>/`), action items
   for the human.

## Session-specific conventions

- No session links in PR bodies or commit trailers.
- `main` is protected: squash merges, linear history; use `/pr-rebase <N>`
  when a PR wedges on "head out of date".
- Docs discipline: completed roadmap items are removed (and renumbered, with
  cross-references fixed) in the same PR that completes them.
```
