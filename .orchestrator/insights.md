# Cross-experiment runtime insights

Experiments observed: obs-followups-2026-07-18, obs-streaming-2026-07-18, simplify-runtime-solid-dry-20260718
Tasks observed (lifetime): 8

Pure aggregation over `.orchestrator/runtime-runs/*/events.jsonl` -- no inference. Regenerate after every experiment via `python3 .orchestrator/lib/insights.py`. Consult this during the /accelerate Routing step; do not let it silently override the manifest -- surface disagreements to the user instead.

## Flags

- mimo: routed 1 task(s), verified zero -- investigate before routing more.
- mimo: escalation rate 100% exceeds 30% flag threshold.
- mimo: only 1 sample(s) -- treat its rates as low-confidence.
- pi_free: routed 2 task(s), verified zero -- investigate before routing more.
- pi_free: escalation rate 150% exceeds 30% flag threshold.
- pi_free: only 2 sample(s) -- treat its rates as low-confidence.

## Per-runtime lifetime stats

| Runtime | Routed | Verified | Failed | Escalated (unresolved) | First-pass success | Duration median (s) | Duration p90 (s) | Escalation rate | Human interventions |
|---|---|---|---|---|---|---|---|---|---|
| codex | 5 | 7 | 0 | 0 | 1.0 | 600.0 | 745.4 | 0.0 | 0 |
| claude | 0 | 1 | 0 | 0 | None | 180.0 | 180.0 | None | 0 |
| grok | 0 | 1 | 0 | 0 | None | 342.6 | 342.6 | None | 0 |
| mimo | 1 | 0 | 0 | 0 | 0.0 | None | None | 1.0 | 0 |
| pi_free | 2 | 0 | 0 | 0 | 0.0 | None | None | 1.5 | 0 |

## By declared risk level

**codex**
- low: 2/2 verified (100%)

## Escalation / retry reasons observed

**mimo**
- escalated x1: mimo run hit the wrapper's 900s timeout with zero stdout/stderr and zero file changes - no verifiable output; retrying on pi_free per one-attempt-then-escalate policy before considering codex

**pi_free**
- escalated x1: pi_free (correct default model this time, root cause fix applied) returned a detailed, specific, plausible-sounding implementation summary (class name, test name, exact pass counts, verification output) but git status shows ZERO file changes in the worktree - a fabricated/hallucinated success report, worse than a silent no-op since it would have been trusted without independent verification. Escalating to codex; respecting codex max_concurrent=1, queued behind obs-persist.
- escalated x1: pi_free attempt exited 0 with empty stdout/stderr and zero file changes in 9.7s on the full task prompt, while a manual retest of the same full prompt exceeded 60s without returning (likely free-tier NVIDIA model instability/rate-limiting under load) - no verifiable output, treating as a failed attempt per one-attempt-then-escalate policy
- escalated x1: pi_free attempt 2 exited 0 but only emitted reasoning text about markdown formatting and never actually wrote any file changes (git diff empty) - two low-risk-runtime attempts (mimo timeout, pi_free no-op) exhausted per one-repair policy; grok is quota-exhausted for today per task4's error; escalating straight to codex
