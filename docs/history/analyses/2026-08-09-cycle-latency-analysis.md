# Where a cycle's wall-clock actually goes

**2026-08-09.** Measured against the first two real Tier 1 runs of
`demos/static-site-v1` in the `aipom-dev` guest, on free OpenRouter models
(`poolside/laguna-s-2.1:free` planning, `impl-gemma-4-31b-it` implementing).

This is a **latency** analysis, not a benchmark. One run on one pin against a
free tier is not a sample, and the free tier's own limits are the single
loudest term in every number below. What it is good for is the *shape* of the
cost: which parts of a cycle are serial, which are wasted, and which would
still be slow on a paid provider.

## The headline

At 61 minutes elapsed, a five-goal cycle had **zero goals promoted**. The plan
was never blocked — it was `running` the whole time.

| Phase | Wall-clock | Notes |
|---|---|---|
| Intent discovery | **13m 04s** | ONE reasoner session, 2–4 model calls |
| Cycle architecture | **23s** | one session, five goals |
| Goal 1 enrichment | **8m 08s** | JIT, serial |
| Goal 2 enrichment | **2m 48s** | started only when goal 1's ended |
| Goals 3–5 enrichment | not reached | three more serial sessions queued |
| Execution (partial) | ~36m | see below |

Execution, over 9 recorded attempts:

| | seconds | share |
|---|---|---|
| Productive agent work (2 succeeded) | 387 | **38%** |
| Wasted on failed attempts (7 failed) | 202 | **20%** |
| Idle, waiting out backoff | 422 | **42%** |

**62% of execution wall-clock produced nothing**, and 6 of 9 attempts failed
classified as `rate_limit`.

## The compounding effect that dominates everything

The retry policy seeded for this roster is `max_backoff_seconds: 900` with
`kind_backoff_scale: {rate_limit: 4.0}`. Those multiply: the ceiling for a
rate-limited task is **3600 seconds**. Measured, exactly:

```
attempt 6   21:21:43
attempt 7   22:21:43      <- one hour later
retry_not_before  23:22:18  <- another hour
```

That is correct behaviour by design — *provider capacity is waiting, not
blocking*, bounded by `execution.provider_outage_ceiling_seconds` (6h) rather
than by an attempt count. The problem is not the curve. The problem is **what
gets put on it**.

A direct call to the same model at the same moment returned **HTTP 200**. The
pi transcript shows the assistant turn coming back with `content: []` and
all-zero token usage. So an empty completion is being classified as
`rate_limit`, and the plan then waits an hour for a limit that does not exist.
The patience is a feature; spending it on a misdiagnosis is the defect. This is
tracked in `docs/architecture/known-issues.md` under *Failure classification*.

## Why the shape is serial

Two independent serializations, both structural rather than incidental:

1. **Enrichment is JIT and one goal at a time.** Goal 2's contract session
   began at 21:02:07, the same second goal 1's committed. At roughly five
   minutes each, five goals is ~25 minutes of pure sequencing before the fifth
   goal can start any work. Execution already runs goals concurrently and
   `ready_goal_ids` already computes the parallelism-safe set — enrichment
   simply does not use it.

2. **Every planning stage is one model round-trip chain.** Discovery spent 13
   minutes on 2–4 calls. One reasoner response carried **22,242 completion
   tokens**. On a free tier that is minutes of generation for a document whose
   useful content was a four-line objective and an eight-item scope list.

## What would actually move the number

Ordered by expected impact, with the cheapest first where impact ties.

1. **Enrich ready goals in parallel.** The single biggest structural win, and
   the machinery exists: `ready_goal_ids` already answers "which goals may
   proceed independently", and the execution loop already honours it. Bounded
   by the same `max_concurrent_goals` the worker already takes. Turns ~25
   minutes of sequencing into roughly one enrichment's worth.

2. **Fix the empty-completion → `rate_limit` misclassification.** Most of the
   42% idle share. A genuine 429 should wait patiently; an empty completion
   should requeue immediately, on the ordinary curve. Note the precedent
   already in the design: a positively identified `REQUEST_CONCURRENCY`
   refusal opens no circuit and requeues impatiently — this is the same
   argument for a different signal.

3. **Rotate models on capacity failure instead of waiting on one.** The roster
   carries four implementers across four models. Tier-ordered routing to
   another capability-satisfying agent whose provider is free already exists
   (un-freeze #16) but engages via the admission gate and circuits; a task that
   simply got rate-limited waits on the *same* binding. Trying the next tier
   converts an hour of waiting into an immediate retry, without mutating the
   persisted binding.

4. **A "the brief is already complete" fast path in discovery.** Thirteen
   minutes produced an intent that restated a brief which had already answered
   every question. Now partly mitigated: `reserved_submit_turns` was extended
   to discovery and architecture on 2026-08-09, so the model is offered turns
   where submitting is its only option instead of reading indefinitely.

5. **Cap generation size.** A 22k-token completion is a latency cost before it
   is a token cost. Submission tools take DTOs; the prompt can say so in
   lengths rather than in adjectives.

6. **Let stage pick the model tier.** `AgentSpec.model_role` (`smart` /
   `cheap`) exists precisely for this. Planning genuinely needs the strong
   model; a scoped, contract-bound implementation task frequently does not.
   This run pinned everything to similar-latency free models, so the
   indirection bought nothing.

## Two operational findings, recorded so they are not rediscovered

- **The worker's logs are block-buffered.** `serve` supervises the worker as a
  subprocess whose stdout is not a tty, so `/tmp/orch.log` sat frozen at the
  startup banner while attempts were running. Diagnosis was impossible until
  the server was restarted under `PYTHONUNBUFFERED=1`. A supervised worker
  should not need an operator to know that.
- **A 31-minute gap between an armed `retry_at` and the attempt.** Attempt 3
  armed `retry_at` 18:45:47; attempt 4 began 19:16:37, with the worker holding
  a live, renewing plan lease throughout and logging nothing in between. Not
  the same thing as a long backoff — the arming timestamp is the thing that was
  not honoured. Undiagnosed; if it is real and general it dwarfs every item
  above.

## What this analysis does not claim

It does not claim these numbers hold on a paid provider — items 2, 3 and the
whole backoff discussion are free-tier-shaped. Items 1, 4, 5 and 6 are
structural and would show up on any provider. It also does not claim the
verification and promotion stages are cheap: this run never promoted a goal, so
their cost is simply unmeasured here.
