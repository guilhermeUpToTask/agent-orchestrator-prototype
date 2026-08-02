# How it works

The shape of one cycle, and why each step exists. If you only read one page
before your first run, read this one.

## The loop

A **project** owns exactly one long-lived **plan**. The plan is never finished —
it goes idle and waits for the next thing you want. Finite delivery work lives
inside a **cycle**, and a cycle runs through five stages:

```
brief  →  intent  →  architecture  →  enrichment  →  execution  →  publication
          ✋ gate     ✋ gate                                        ✋ gate
```

**Intent.** You describe what you want in chat. The reasoner asks questions back
and normalizes the conversation into a written proposal: objective, scope,
constraints, exclusions. Your messages are saved *before* the model is called,
so a crash mid-conversation loses nothing. You approve, edit, or cancel it.

**Architecture.** A planning session reads your repository — actual files, at a
committed ref, never your uncommitted working tree — and submits an ordered set
of goals with real dependency edges between them. You approve that draft, and
approving it is what creates the cycle.

**Enrichment.** Just before a goal runs, and only for the goal that is next, the
reasoner freezes a contract: what "done" means, which paths may be touched,
which must not be weakened, what commands verify it, and which of three
verification modes applies (`tdd`, `characterization`, or `executable_check`).
Frozen late, so it reflects the repository as it is *now* rather than as it was
when the cycle was planned.

**Execution.** A worker picks up the earliest unfinished goal and runs its tasks
in order. Each attempt gets a fresh git worktree. The agent's output is a
**candidate**, never a result — before anything is kept, the orchestrator
independently re-runs the verification command, checks that protected tests were
not weakened, checks nothing outside the allowed scope was touched, and records
the exact command, its exit code, and the commit SHAs it ran against.

**Publication.** When every goal is accepted, you get one last gate and record
what should happen to the work: open a PR, merge, keep the branch, or discard.

## Where the code goes

Work climbs a ladder of git branches, and **only independently verified work
moves up a level**:

```
your default branch          ← never written by plan work
  └── cycle/<cycle-id>       ← one per cycle, cut from the default branch
        └── goal/<goal-id>   ← merged in only when every task is DONE
              └── task/<task-id>/<run-id>
```

A failed attempt is discarded whole — worktree and branch deleted — so a retry
starts clean and leaves no trace. That is why a failed attempt costs you nothing
to inspect: there is nothing to inspect.

Your default branch is never touched. See [evidence.md](evidence.md) for how to
find the branch afterwards, which depends on how the project was bound.

## What makes this different from asking a model to write code

Three things, and they are all about not trusting the agent:

1. **The agent never decides it succeeded.** It produces a candidate. The
   orchestrator runs the verification command itself and keeps its own record.
2. **Tests are proven to fail first.** Under `tdd`, the test is authored in its
   own commit and must go RED before the implementation makes it GREEN. A test
   that passes against an unimplemented feature proves nothing, and this catches
   it.
3. **Scope is enforced, not requested.** The contract names what may change.
   Work that reaches outside it is rejected, not merged with a warning.

The result is that "it works" is a claim you can check, not one you have to
accept. [evidence.md](evidence.md) shows you how to check it.

## When things go wrong

Failure is normal and mostly handled without you. A failed task retries on a
backoff curve, with the orchestrator's own rejection reasons fed back into the
next prompt. A rate limit or quota exhaustion is treated as *waiting*, not
failing — the work pauses on an automatic backoff and resumes. An unsatisfiable
contract is repaired in place, twice, before anyone is asked.

You are asked only when those are exhausted, and then you get a **block**: which
stage failed, which goal and task, what evidence exists, and which actions are
legal. One blocked goal does not stop an independent sibling goal.

[statuses.md](statuses.md) explains what each state means and what to do in it.

## Next

- [gates.md](gates.md) — the three decisions you will be asked to make
- [statuses.md](statuses.md) — what the console is telling you
- [evidence.md](evidence.md) — what the system can prove, and how to check it
- [getting-started.md](getting-started.md) — install and run one
