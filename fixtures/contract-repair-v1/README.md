# Contract repair v1 — does a repairable contract actually get repaired?

Every other fixture drives a run that *succeeds*. This one drives a run that
**must fail first**, because the recovery machinery worth the most is the part
that only executes when something is wrong.

|  |  |
|---|---|
| **Proves** | an unsatisfiable contract is repaired in place, recorded, and the task then completes — no human asked |
| **Tier** | **1 only** (`reasoner.mode=llm`, `agent_runner.mode=real`) — see "Why not Tier 0" |
| **Cost** | one real cycle on the configured models |
| **Interface** | `GET/POST /api/…` via `curl` + `jq` |
| **Target repo** | the happy-path-v1 seed (`materialize.sh` from that fixture first) |

## Why it exists

`src/app/contract_repair.py` (un-freeze #17) exists so a contract no agent could
satisfy is fixed in place rather than escalated to a person. Until this fixture
there was **no run evidence that it worked** — and when a Tier 1 series finally
provoked it on 2026-07-27, it did not: the repair write self-deadlocked SQLite,
every attempt was abandoned, the same repair was recomputed until the retry
budget ran out, and the goal blocked. The feature that exists to keep a
repairable contract away from a human was what blocked the human in.

That defect is fixed. This fixture is how it stays fixed, and how the next
repair class gets validated before shipping rather than after.

## The trigger, and why it is an edit

The seed repository's test file is `tests/test_greeter.py`. The fixture pauses
the plan once the head task's contract is frozen and rewrites exactly one field:

```json
{"type": "update_task_contract",
 "verification_commands": ["python -m pytest -q tests/test_greet.py"]}
```

`test_greet.py` does not exist. The command can never pass, no matter what the
agent writes — which is precisely the shape `propose_repair` recognises as
CONTRACT-shaped rather than candidate-shaped, and repairs by snapping the
command to the near-twin path that is actually tracked.

This is not a contrived defect. It is one of the two classes observed from a
real reasoner in a live Tier 1 run, and it is exactly what repository drift
looks like: a contract that was satisfiable when frozen stops being so when a
file is renamed.

## Why not Tier 0

**Tier 0 cannot reach this, and the reason is itself a recorded finding.**

1. The stub freezes `allowed_scope: ["."]` and
   `verification_commands: ["test -f <dry-run marker>"]` — satisfiable by
   construction. Nothing the dry-run runner does can fail it.
2. Poisoning it therefore requires the operator edit above, and the edit
   requires a PAUSED plan with an already-frozen contract. That window does not
   exist at Tier 0: a worker enriches the goal and executes its first task under
   ONE claim, the pause gate blocks *claims*, and the whole goal finishes in
   about six seconds. Arming a pause before enrichment stops enrichment too.

That is the "no operator control point at the contract boundary" item deferred
in [ROADMAP.md](../../ROADMAP.md). Tier 1 works only because a real attempt
takes tens of seconds, so the pause lands between attempts. **This fixture is
concrete evidence for why that control point is worth building** — it is the
difference between a deterministic free regression test and one that needs a
paid runtime and a timing window.

## What it asserts

1. The poisoned command produces a **contract-shaped** failure
   (`authoritative verification command failed`), not a candidate rejection.
2. A `contract_repair` planning artifact is written with `outcome: committed` —
   the assertion that would have caught the deadlock, since before the fix the
   repair was recomputed forever and never persisted.
3. The frozen contract now names the **real** path, `tests/test_greeter.py`.
4. The task reaches DONE and **no block was opened** — nobody was asked.
5. The worker log contains **zero** `Database stayed locked` events.

## Run it

```bash
# once: the target repo (shared with happy-path-v1)
./fixtures/happy-path-v1/scripts/materialize.sh

# Tier 1 config, then start the backend
backend/scripts/dev.sh start

./fixtures/contract-repair-v1/scripts/run.sh
```

Exit 0 = repaired and green. Exit 1 = a numbered assertion failed. Exit 2 =
setup (wrong tier, no repo, API down).

`HAPPY_PATH_API` and `HAPPY_PATH_REPO` are honoured, same as the other fixtures.
`CONTRACT_REPAIR_WORKER_LOG` points the deadlock check at your worker log; when
unset, assertion 5 is reported as skipped rather than silently passing.

## What it deliberately does NOT do

It does not test the *second* repair class (a TDD strategy whose `allowed_scope`
forbids the test path). That one needs the agent to attempt an authoring stage
and be refused, which takes a second poisoned field and a longer run; the
command near-miss is the cheaper of the two and exercises the same
propose → amend → record → requeue path end to end.

It also does not assert the repair BOUND (two distinct repairs, then a block).
`test_repair_is_bounded_and_still_ends_in_a_block` covers that on both backends
and does not need a provider.
