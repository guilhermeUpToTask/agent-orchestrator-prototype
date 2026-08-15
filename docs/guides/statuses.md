# Reading the console

What each state means, and what you can do in it. If the console is showing you
something and you cannot tell whether to act, this page is the answer.

## The five statuses

A plan has exactly one stored status. Everything else on screen — what it is
working on, which goal, which task — is **derived fresh** every time it is read,
so it can never drift out of sync with reality.

| Status | Means | Usually you |
|---|---|---|
| `idle` | Nothing in flight. The plan is waiting for you to want something. | start an intent |
| `waiting` | It needs a decision from you: a gate, or a conversation turn. | answer it |
| `running` | A worker holds it and work is advancing. | watch, or pause |
| `paused` | You paused it, or it auto-paused recoverably. | resume, edit, or replan |
| `blocked` | Every remaining goal is blocked or depends on one that is. | resolve the block |

**There is no `failed` status.** A plan is a long-lived thing; individual work
fails, gets retried, or gets blocked for a human — but the plan itself never
reaches a dead end you cannot act on.

## Legal actions

The console never guesses which buttons to show. The API says which actions are
legal right now, and the console renders exactly those. If a button is missing,
the action is genuinely not available — not hidden.

| Situation | What is offered |
|---|---|
| `running` | `pause`, `start_replan` |
| `paused` (a real manual pause) | `resume`, `start_replan`, `edit_pending_work` |
| `idle`, or waiting with nothing open | `start_intent` |
| a gate is open | the gate's own decisions — approve, edit, cancel |
| blocked | that block's legal resolutions, and nothing wider |
| a goal promotion is mid-flight | `pause` only, until the merge lands |

`resume` removes a manual pause and nothing else. It does not retry a failed
task, clear a backoff, or resolve a block — those are separate, deliberate
commands, so that "unpause" can never quietly mean "and also retry the thing
that failed".

## Waiting is not blocked

This distinction matters more than any other on this page.

**Waiting** is the system being patient. A rate limit, a quota exhaustion, or a
connection failure opens a circuit and the work waits on automatic backoff, with
a single probe testing whether the provider recovered. This is bounded by
wall-clock ceilings — hours, not attempt counts — and inside that window a
capacity failure does not spend the task's retry budget. **You do not need to do
anything.** The activity will say `provider_waiting`.

**Blocked** is the system having run out of options and asking for you. It comes
with structure: which stage, which goal and task, which run, what evidence
exists, and an explanation written to be read by an operator rather than a
developer.

A blocked plan is the last resort, and reaching one means retries, contract
repair, and promotion re-attempts were all exhausted first.

## Blocks are per goal

One goal blocking does not stop an unrelated sibling goal. The plan only settles
`blocked` when **every** non-terminal goal is either blocked itself or depends
on one that is.

So a plan showing `running` with a block visible is not a contradiction: one goal
needs you, and others are still making progress. The actions offered will be that
block's resolutions plus `pause` and `start_replan`.

## What the activity field is telling you

`status` says whether it needs you. `activity` says what it is doing.

| Activity | Meaning |
|---|---|
| `intent_discovery` | in conversation, shaping the brief |
| `cycle_architecture` | planning goals, or waiting on the draft gate |
| `replan_discovery` | conversational replan, source cycle retained |
| `goal:<id>` | freezing that goal's contracts |
| `task:<goal>:<task>` | that task is executing |
| `provider_waiting` | on a capacity backoff — patient, not stuck |
| `cycle_verification` | all goals done, publication gate next |
| `blocked:<stage>` | needs you, at that stage |
| `idle` | nothing in flight |

## When it looks stuck

Check in this order:

1. **Is a worker running?** No worker means nothing advances, and the plan will
   sit in `running` looking healthy. `praxis serve` runs one for you.
2. **Is the activity `provider_waiting`?** Then it is waiting on purpose. Leave
   it.
3. **Is there a backoff in flight?** A failed task retries on a curve, in strict
   order within its goal. Later goals wait for the head goal by design.
4. **Is a gate open?** A plan in `waiting` is waiting on *you*.

[troubleshooting.md](troubleshooting.md) covers specific symptoms and error
codes.
