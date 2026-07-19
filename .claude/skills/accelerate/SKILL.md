---
name: accelerate
description: Rapidly execute an approved development plan by routing independent tasks across in-process Claude Sonnet subagents, Codex CLI, pi/OpenRouter/NVIDIA, MiMo, Grok, and other configured coding runtimes according to live quota, capability, risk, and recent success — cross-checked against the lifetime insights rollup and a shared-abstractions ledger that stops parallel branches from reinventing the same helper. Use for temporary high-throughput multi-agent development.
argument-hint: "[plan file or objective]"
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

# Accelerated runtime routing

Execute `$ARGUMENTS` using the configured runtime pool. Optimize verified progress, not raw parallelism.

## Preconditions

1. Read `CLAUDE.md`, `.orchestrator/runtime-pool.yaml`, the approved plan, and relevant project specifications/decision records.
2. Regenerate and read `.orchestrator/insights.md` (`python3 .orchestrator/lib/insights.py`, no arguments — aggregates every past experiment). It is a deterministic rollup, not a recommendation engine: read the flags and per-risk success rates yourself and decide, don't treat a flag as an automatic exclusion.
3. Read `.orchestrator/shared-abstractions.md`. Grep it, and the target module tree, for keywords matching the plan's tasks before writing any packet — this is how duplicated "solved it independently on each branch" work gets prevented, not caught after the fact.
4. Inspect the repository and current Git state. Preserve existing work. If other Claude Code sessions may be active on this repo (check `ps aux` for other `claude` processes, check `git reflog` for commits you didn't make), treat the branch tip as something that can move between your reads and your merges — re-check immediately before every merge, never assume a snapshot is still current.
5. Run each configured runtime's probe. Never infer quota from plan names. If quota cannot be queried automatically, use the manifest's manually recorded snapshot and mark its confidence.
6. Test an unfamiliar CLI with a read-only trivial request before assigning repository changes.

## Task preparation

Convert the approved plan into bounded task packets containing:

- objective and acceptance criteria;
- dependencies and risk: low, medium, high, critical;
- relevant paths and prohibited paths;
- required capabilities;
- authoritative verification commands;
- maximum attempts and escalation target;
- any hits from `.orchestrator/shared-abstractions.md` — named explicitly as "MUST REUSE: `<primitive>` at `<location>` — do not reimplement."

Do not ask delegated agents to redesign the approved architecture. Return ambiguity to Claude or the user.

### Choosing which tasks actually run in parallel

Before dispatching a wave, group the candidate tasks by **concern signature** — the keywords
and subtree they touch (the same axis `shared-abstractions.md` is indexed by), not just by
which files they edit. Two tasks with disjoint file paths can still collide in concern (e.g.
one task adds output-bounding to module A, another adds it to module B) — that is exactly how
the duplicated `_BoundedBuffer`/`_BoundedLog`/`safe_runtime_tail`-shaped logic happened here.

- Same concern + overlapping subtree → never parallel. Sequence them (one packet owns the new
  primitive; the other's packet is written against it) or merge into one packet.
- Same concern + unrelated subtrees → parallel is fine, but flag it in the routing decision so
  the ledger update step reconciles whichever primitive lands second against the first.
- Disjoint concern + disjoint paths → the only case that's free to parallelize without a
  reconciliation step.

Pick the highest-value disjoint set for the next wave, not just whatever is independent —
optimize for verified throughput per concurrent slot (see Routing), not for maximizing how many
tasks are technically parallelizable.

## Routing

- Claude remains coordinator and handles ambiguity, architecture, security, concurrency, migrations, and final synthesis.
- Claude may also route bounded, low/medium-risk tasks to **`claude_sonnet`** subagents (the
  Agent tool, `model: "sonnet"` always — never opus/haiku for this lane) instead of only
  delegating externally. This is a routing lane like any other in the manifest, not a
  fallback: prefer it over spending external CLI quota for reconnaissance, docs, fixtures,
  narrow/mechanical implementation, and verification fan-out.
  - Spawn subagents with `run_in_background` and batch independent `Agent` calls into one
    message so they actually run concurrently, up to `claude_sonnet.max_concurrent`.
  - Token optimization is not optional here: give each subagent only its task packet plus the
    specific file paths/line ranges it needs — never the full conversation, never unrelated
    files. Use a read-only Explore agent for reconnaissance and a separate writing subagent
    only for the implementation step. Ask for a bounded final report ("under N words") so
    returning results doesn't blow up the coordinator's own context.
  - Same rules as every other lane: its own worktree, its own branch, verified before merge.
- Prefer inexpensive/free runtimes (or `claude_sonnet`) for documentation, fixtures, mechanical edits, narrow tests, repository reconnaissance, and other low-risk tasks.
- Prefer Codex for medium/high-risk implementation, multi-file refactors, debugging, and review while its quota remains available.
- Route only to a runtime whose probe is healthy and whose capabilities satisfy the packet.
- Consider quota remaining, reset time, risk, context requirement, historical success (check `.orchestrator/insights.md`), and expected repair cost.
- Reserve at least the configured quota floor for emergency repair/review.
- A free runtime gets one implementation attempt and at most one evidence-driven repair. Escalate rather than loop.

## Isolation and concurrency

- Use one Git worktree and task branch per writing agent — including `claude_sonnet` subagents.
- Never allow concurrent writers in the same worktree or overlapping path ownership.
- Parallelize only dependency-independent, non-overlapping tasks (see "Choosing which tasks
  actually run in parallel" above — path-independence alone is not sufficient).
- **Before dispatching a wave, sum the concurrent slots across every runtime you intend to use
  and confirm the total is ≤ `global.max_parallel_writers`.** Each runtime also has its own
  `max_concurrent` ceiling in the manifest (e.g. `codex: 1`, `claude_sonnet: 3`) — respect both:
  the per-runtime ceiling bounds how many of that one runtime run at once, the global ceiling
  bounds the wave as a whole. A wave of "1 codex + 1 mimo + 2 claude_sonnet" is 4 total writers;
  check that against the current `max_parallel_writers` before dispatching, don't assume.
- Give agents minimal context: task packet, relevant spec excerpts, exact paths, and commands.
- Do not include secrets or provider credentials in prompts or logs.

## Verification and integration

Verification is external to the implementing agent. Collect the diff, commands, exit codes, test results, and concise runtime summary. Reject unrelated changes. Verify diffs **against their own worktree with the project's real environment/venv**, never against the coordinator's main working directory — if another task or session has also touched the main tree, checking there verifies the wrong file and passes for the wrong reason. Merge into the goal/integration branch only after authoritative checks pass. Re-check the branch tip immediately before each merge (see Preconditions #4) — a plain `git merge` is safe against concurrent commits (it won't discard them), but confirm there's no conflict before assuming a clean fast pass. Stop at human gates defined by the project.

**After a task's diff is verified and merged**, update `.orchestrator/shared-abstractions.md`: scan the diff for new small standalone functions/classes that generalize past this one task and append them to the index. This is what makes the ledger useful to the *next* wave instead of just this one.

Every runtime invocation MUST go through `.orchestrator/lib/runtime_wrapper.py` — it is the
single choke point that records routing decisions, quota snapshots, invocation metadata,
captured stdout/stderr, Git changes, verification evidence, retries/escalations, human
interventions, and final outcomes as append-only JSONL under
`.orchestrator/runtime-runs/<experiment_id>/events.jsonl` (large output goes to sibling
`logs/` files, referenced by path). Never call a runtime CLI directly, and never let a
delegated agent write its own run record — the wrapper is the only writer, so evidence can't
be skipped or fabricated. This applies to `claude_sonnet` subagents too: record their
`routing-decision`/`task-outcome` events through the wrapper even though the invocation itself
is an in-process Agent tool call, not a subprocess — the wrapper's `run` subcommand is for
CLI-shaped runtimes only, so log a subagent's start/end/outcome directly via the
`routing-decision`/`git-change`/`task-outcome` subcommands around your own Agent tool call.
Unknown quota values are always `null` + `"quota_status":
"unavailable"`, never coerced to zero or estimated. Use this evidence to update runtime
success estimates, but do not modify routing policy silently. After an experiment, run
`.orchestrator/lib/report.py <experiment_id>` for a deterministic within-experiment comparison,
**then** `.orchestrator/lib/insights.py` (no arguments) to fold it into the lifetime
cross-experiment rollup at `.orchestrator/insights.md` that the next run's Preconditions step
reads.

Notes on runtimes with non-obvious invocation: `claude`'s and `claude_sonnet`'s `command: null`
in the manifest both mean "use the Agent tool," not a CLI shell-out — `claude` is the
coordinator itself (this session), `claude_sonnet` is a spawned subagent with an explicit
`model: "sonnet"` override; don't conflate the two entries. `mimo` must stay pinned to its
native `mimo/mimo-auto` model — routing it through the `anthropic`/`openrouter` pass-through
providers fails (401) because those need credentials mimo's own store doesn't have.

## Completion report

Report completed and blocked tasks, verification evidence, branches/worktrees, quota state, failures/escalations, and the next highest-value runnable tasks. Do not claim success from an agent's prose; require repository and test evidence. Note any new entries added to `.orchestrator/shared-abstractions.md` and any flags surfaced by the regenerated `.orchestrator/insights.md`.
