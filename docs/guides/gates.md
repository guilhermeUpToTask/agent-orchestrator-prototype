# The three gates

Every consequential step waits for you. There are exactly three of these, they
always arrive in the same order, and none of them can be skipped.

A gate is bound to an exact revision of the thing it reviews. If you edit the
subject, the revision moves and the old approval no longer applies — you cannot
accidentally approve version 2 by clicking a button rendered for version 1.

---

## 1. The intent gate

**What it asks:** is this the right problem, described correctly?

You get a written proposal — objective, scope, constraints, exclusions — that
the reasoner distilled from your chat. It is not code yet, and nothing has been
planned around it.

**Your options:** approve, edit, or cancel.

**What to actually check:**

- Is the **scope** right? It becomes the boundary the planner works inside — too
  wide invites unrelated changes, too narrow forces work that cannot fit.
- Are the **exclusions** the ones you meant? This is the cheapest place to say
  "don't touch the auth module".
- Does the **objective** describe an outcome rather than an implementation? You
  are about to let a planner choose the implementation.

This is the cheapest gate to reject at. Everything downstream is derived from
it, so a wrong intent is wrong work in five places later.

---

## 2. The cycle draft gate

**What it asks:** is this a sensible plan for that intent?

A planning session has read your repository at a committed ref and produced an
ordered list of goals with real dependency edges. Approving it **creates the
cycle** — this is the point where the plan starts being able to run.

**Your options:** approve, edit, or cancel.

**What to actually check:**

- Is the **order** right? Position is the scheduling barrier: only the earliest
  unfinished goal advances, and a goal that fails blocks the ones after it.
- Are goals **independently sized**? Two goals that touch the same files cannot
  usefully run in parallel.
- Is anything **missing** that you will notice only at the end? Adding it now is
  an edit; adding it later is a replan.
- Does any goal look like it will need something the scope forbids?

Goals are not contracts yet. Task-level detail is frozen just before each goal
runs, so you are approving a shape, not a specification.

---

## 3. The publication gate

**What it asks:** what should happen to the work?

Every goal has been accepted, with evidence. The cycle branch holds the result.
You record one disposition:

| Disposition | Means | Requires a reference |
|---|---|---|
| `open_pr` | you opened, or will open, a pull request | yes |
| `merge` | you merged it yourself | yes |
| `retain_branch` | keep the branch, decide later | yes |
| `discard` | throw the work away | no |

**Be clear about what this does and does not do.** Recording `open_pr` or
`merge` **does not open or merge anything** — the orchestrator has no
authenticated access to your forge, by design. It records what *you* did. The
branch is already sitting in a repository; the delivery panel on this gate tells
you which one and gives you the commands.

The reference is free text you type, and it is currently the one place in the
whole system where a human asserts something nobody verified.

**What to actually check:** read the evidence first. Exact commands, exit codes,
candidate and test commit SHAs are all served — and you can re-ask git whether
any of it is true. [evidence.md](evidence.md) shows how.

Recording the disposition returns the plan to idle, ready for the next cycle.

---

## Changing your mind

Gates are not the only way to intervene, and they are not reversible after the
fact. Between them you can:

- **pause** — graceful; a running attempt finishes, then the plan settles
- **edit** a pending task or goal while paused
- **retry** one named failed task
- **replan** — a new conversation producing a new cycle. The source cycle stays
  visible and immutable, and its completed work is given to the reasoner as
  context so it is not rebuilt

Replan is the big hammer: holistic, conversational, and it supersedes the cycle
when the new one activates. An edit is surgical. Prefer the edit.
