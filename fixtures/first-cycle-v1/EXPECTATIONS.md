# first-cycle-v1 — what counts as success

Binary. A run either satisfies these or it is a finding. `scripts/verify_run.py`
checks 1–6 from served facts and git; `--tier 1` adds 7.

| # | Expectation | Checked by |
|---|---|---|
| 1 | The cycle reached publication and a disposition was recorded | `verify_run.py` |
| 2 | Every DONE task carries accepted, revision-bound evidence naming the exact command that ran | `verify_run.py` |
| 3 | Each goal branch merged into the cycle branch at the SHA the API serves — a real merge commit, found by `git cat-file` | `verify_run.py` |
| 4 | The seed repository's default branch is identical to `first-cycle-v1-seed` | `verify_run.py` |
| 5 | The plan settled IDLE with no block outstanding | `verify_run.py` |
| 6 | One goal (two tolerated), at most three tasks in it | `verify_run.py` |
| 7 | **Tier 1 only:** `slugify` is actually implemented on the promoted tree | `verify_run.py --tier 1` |
| 8 | The six critical-defect guards hold against the live API | `guards.sh` |

## Size budget

The brief asks for one goal and the smallest task set. Two goals is tolerated;
**three is a failure**, and so is a goal with more than three tasks. This is not
style policing: a reasoner that answers a twelve-line brief with a platform
rewrite has misread the constraint, and pushing the run through anyway is how a
fixture stops being a fixture. Reshape the draft (`PUT …/cycle-draft`) or cancel
it and say so in the conversation.

## Expectation 7 is the one that separates the tiers

Tier 0 (stub + dry-run) satisfies 1–6 and **cannot** satisfy 7: the dry-run
runner promotes branches without writing an implementation, which is exactly
what makes it free and deterministic. A Tier 0 run proves the *lifecycle*. Only
Tier 1 proves the *product*.

Never mix the halves. `preflight.sh` fails a mixed pair on purpose: a real
reasoner verified by a dummy runner, or a dry-run plan spending real tokens,
produces evidence that means nothing.

## What this fixture does NOT cover

- **Recovery paths.** No induced failure, no retry, no block resolution, no
  replan. `planning-recovery-v1` and `contract-repair-v1` own those.
- **Parallelism.** One goal by construction; `parallel-goals-v1` owns two.
- **The frontend.** API only. If a run needs the UI to complete, that is itself
  a finding — the UI is a second client of these same endpoints.
- **Forge publication.** `retain_branch` is the default disposition because no
  authenticated forge port exists; `open_pr` and `merge` record a reference for
  an operation someone else performed.

## Known variability on free models

Free-tier models are slow and rate-limited, not broken. A run that sits in
`provider_waiting` is recovering on its own — the run script prints it and keeps
waiting, and only a `requires_human` block ends the run. Budget 10–40 minutes and
expect discovery to take one or two extra conversational turns; the script
answers them.
