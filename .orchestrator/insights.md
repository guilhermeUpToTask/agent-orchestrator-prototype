# Cross-experiment runtime insights

Experiments observed: obs-followups-2026-07-18, obs-streaming-2026-07-18, roadmap-accel-2026-07-20, roadmap-clarity-2026-07-20, roadmap-exec-2026-07-19, simplify-runtime-solid-dry-20260718, walkthrough-2026-07-19, walkthrough-api-2026-07-20
Tasks observed (lifetime): 33

Pure aggregation over `.orchestrator/runtime-runs/*/events.jsonl` -- no inference. Regenerate after every experiment via `python3 .orchestrator/lib/insights.py`. Consult this during the /accelerate Routing step; do not let it silently override the manifest -- surface disagreements to the user instead.

## Flags

- codex: 1 human intervention(s) recorded -- check events for root cause.
- mimo: routed 1 task(s), verified zero -- investigate before routing more.
- mimo: escalation rate 100% exceeds 30% flag threshold.
- mimo: only 1 sample(s) -- treat its rates as low-confidence.
- pi_free: routed 2 task(s), verified zero -- investigate before routing more.
- pi_free: escalation rate 150% exceeds 30% flag threshold.
- pi_free: only 2 sample(s) -- treat its rates as low-confidence.

## Per-runtime lifetime stats

| Runtime | Routed | Verified | Failed | Escalated (unresolved) | First-pass success | Duration median (s) | Duration p90 (s) | Escalation rate | Human interventions |
|---|---|---|---|---|---|---|---|---|---|
| codex | 16 | 19 | 1 | 0 | 0.842 | 680.0 | 900.0 | 0.053 | 1 |
| claude_sonnet | 11 | 11 | 0 | 0 | None | 91.0 | 226.0 | None | 0 |
| grok | 3 | 4 | 0 | 0 | 0.5 | 321.3 | 700.0 | 0.0 | 0 |
| claude | 0 | 1 | 0 | 0 | None | 180.0 | 180.0 | None | 0 |
| mimo | 1 | 0 | 0 | 0 | 0.0 | None | None | 1.0 | 0 |
| pi_free | 2 | 0 | 0 | 0 | 0.0 | None | None | 1.5 | 0 |

## By declared risk level

**codex**
- low: 3/3 verified (100%)
- medium: 8/9 verified (89%)
- high: 1/1 verified (100%)

**claude_sonnet**
- low: 10/10 verified (100%)
- medium: 1/1 verified (100%)

**grok**
- low: 3/3 verified (100%)

## Escalation / retry reasons observed

**codex**
- escalated x1: codex exec workspace-write wraps every command in bubblewrap, which cannot create namespaces in this devcontainer (bwrap: No permissions to create new namespace); ALL file ops failed, zero changes — codex unusable for writes here. It fetched ROADMAP.md from GitHub main via MCP, revealing the working branch is STALE vs origin/main (missing PRs #33/#34/#35, which already restructured the roadmap). Escalating to coordinator + user decision on branch reconciliation.
- retried x1: coordinator harness error: relative --cd path resolved against wrapper cwd; retry with absolute path, not a runtime fault

**grok**
- retried x1: same coordinator harness error: relative --cwd path; retry with absolute path

**mimo**
- escalated x1: mimo run hit the wrapper's 900s timeout with zero stdout/stderr and zero file changes - no verifiable output; retrying on pi_free per one-attempt-then-escalate policy before considering codex

**pi_free**
- escalated x1: pi_free (correct default model this time, root cause fix applied) returned a detailed, specific, plausible-sounding implementation summary (class name, test name, exact pass counts, verification output) but git status shows ZERO file changes in the worktree - a fabricated/hallucinated success report, worse than a silent no-op since it would have been trusted without independent verification. Escalating to codex; respecting codex max_concurrent=1, queued behind obs-persist.
- escalated x1: pi_free attempt exited 0 with empty stdout/stderr and zero file changes in 9.7s on the full task prompt, while a manual retest of the same full prompt exceeded 60s without returning (likely free-tier NVIDIA model instability/rate-limiting under load) - no verifiable output, treating as a failed attempt per one-attempt-then-escalate policy
- escalated x1: pi_free attempt 2 exited 0 but only emitted reasoning text about markdown formatting and never actually wrote any file changes (git diff empty) - two low-risk-runtime attempts (mimo timeout, pi_free no-op) exhausted per one-repair policy; grok is quota-exhausted for today per task4's error; escalating straight to codex
