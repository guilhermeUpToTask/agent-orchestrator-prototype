# Parallel goals v1 — does a goal merge survive a moved cycle branch?

Every other fixture runs one goal. One goal can never exercise the thing that
makes goal promotion interesting: **the second goal merges into a cycle branch
the first one already moved.**

|  |  |
|---|---|
| **Proves** | two independent goals execute and both promote into one cycle branch |
| **Tier** | **0 only** (`reasoner.mode=stub`, `agent_runner.mode=dry-run`) |
| **Cost** | free, no provider calls |
| **Interface** | `GET/POST/PUT /api/…` via `curl` + `jq` |

## Why it exists

`goal_promotion_failure` is the one block kind with **no observed run evidence**.
It advertises a single resolution (`start_replan`) and opens on the first
exception from `merge_goal` — no retry at all, including for a transient git
error. Auto-rebasing the goal branch and re-verifying before a merge retry is the
obvious repair, but it is also the largest and riskiest change left: goal→cycle
promotion currently runs **no verification of its own**, so a rebase would mean
introducing one, and skipping it would move unverified work upward.

The ROADMAP's rule for this phase is not to expand the system without run
evidence. This fixture is how that evidence gets collected — or how we learn the
failure is not reachable in the normal case and the repair is not worth building.

## Two goals without a real model

The stub reasoner only ever produces one goal. It does not have to: a cycle draft
sits at a **review gate**, where nothing is executing, so the draft can simply be
revised through `PUT /api/plans/{id}/cycle-draft` into two goals with no
dependency between them, then approved. No race, no model, no cost.

## What it asserts

1. **Both goals reach DONE** — goal parallelism (ADR-001) actually runs.
2. **Both goal branches are merged into the cycle branch**, so the second merge
   happened against a base the first had already moved.
3. **No block of any kind was raised.**

## What it deliberately does NOT do

It does not manufacture a merge conflict. Under dry-run each task writes its own
distinct artifact, so two goals never touch the same file and the merge cannot
conflict — which is itself part of the finding. A genuine conflict needs real
agents editing overlapping scope (Tier 1, two goals), and inventing one here by
hand would be testing git, not the orchestrator.
