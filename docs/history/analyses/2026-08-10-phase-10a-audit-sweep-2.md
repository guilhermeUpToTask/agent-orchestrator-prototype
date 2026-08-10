# Phase 10A audit — sweep 2: the lease and goal-lease under real contention

**Date:** 2026-08-10
**Scope:** the plan claim (`claim_one_unit`), the goal lease, heartbeat and
release, under genuine concurrency against real SQLite.
**Result:** **no defect in the lease.** The mutual-exclusion properties hold
under every race constructed. What the sweep produced instead is the coverage
that was missing, **two defects in the test suite** that only appeared once the
suite was loaded and run repeatedly (F7, F8), one recorded asymmetry, and three
retractions.

Both suite defects share a shape worth naming: a test that asserted something
slightly *stronger* than the behaviour it was written to protect — a fixed
one-second window instead of "the beat advances", and "the machine is clean"
instead of "this run cleaned up". Neither over-assertion was visible while the
suite was quiet.

This was named the head of the queue by sweep 1 and the roadmap's own priority
list: *"concurrency around the lease and goal-lease interaction under real
contention"*. It is the area most likely to hide something and the most
expensive to prove — which is exactly why "we looked and it holds" is worth
writing down rather than leaving as an untested assumption.

---

## What was tested, and how

Each simulated worker gets its **own engine and connection pool**, released
through a `threading.Barrier` so the attempts genuinely collide. That matters:
threads sharing one engine race inside one pool, which is not the shape two
`orchestrate worker start` processes have.

| Race | Setup | Result |
|---|---|---|
| One claimable plan, N workers | 16 in the probe, 6 in the test | exactly **1** winner |
| M plans, N workers (N > M) | 8 plans, 16 workers | **8** claims, 8 distinct, **0** double-claims, 0 errors |
| One goal lease, N workers | 16 | exactly **1** winner |
| Expired lease, N reclaimers | 16 | exactly **1** winner |
| Live lease, N thieves | 6 | **0** winners |
| Displaced holder heartbeats | — | refused; the thief keeps the lease |

The N figures above are the throwaway probe's, which pushed harder than the
committed tests do; the tests keep the same races at 6 workers (and 5 plans for
the multi-plan one) because that proves the same properties in ~0.6s instead of
up to 35s — see the cost note below.

No double-claim, no lost update, and no `DB_LOCKED` exhaustion was produced in
any configuration.

## The coverage that was missing

The goal lease already had a two-thread race
(`test_goal_lease_repository.py::test_two_sqlite_repositories_racing_for_one_goal_have_one_winner`).
**The plan claim had none** — every existing test drove one repository,
serially. `claim_one_unit` is the function that decides which worker advances
which plan, and nothing had ever run two of them at once.

`tests/unit/orchestration/test_plan_claim_contention.py` closes that with the
five races in the table above.

**These tests were verified capable of failing.** Removing the
`claimed_by IS NULL OR lease_expires_at < :now_epoch` predicate from `_CLAIM_SQL`
fails 4 of the 5 immediately; the predicate was then restored and the file
confirmed byte-identical to `HEAD`. A concurrency test that has never been seen
to fail is decoration, and this phase should not add any.

Stability and cost: 15 consecutive runs at 12 workers passed, but wall time
ranged **0.66s to 34.77s** — SQLite's own `busy_timeout=5000` being paid up to
`MAX_LOCK_RETRIES` times, not application backoff, which caps at 1.55s. The
committed tests use **6** workers, which proves the same properties in a steady
~0.6s. The variance is worth knowing about before someone adds a heavier
contention test to CI.

## F7 — a load-sensitive test in the suite, found by loading it

**Severity:** CI reliability.
**Status:** fixed.

`test_worker_pool.py::test_a_worker_keeps_beating_while_a_goal_is_running`
sampled the worker registry 20 times at 0.05s — a **fixed ~1s wall-clock
window** — and asserted it saw two distinct `last_seen_at` values. Adding the
contention tests above to the suite made it fail **2 runs in 4**; on the same
machine, without them, 2 runs in 2 passed.

**Mechanism.** `last_seen_at` is microsecond-precision, and at the test's
patched 0.05s beat roughly 20 heartbeats are expected in that window, so the
assertion needs only 2 of ~20. Failing it means the worker's event loop was
starved for essentially the whole second. `worker/main.py` says why that is
possible in its own comment: *"SQLite access is synchronous… an unlucky
writer-lock handoff can block this event-loop thread"*. The new contention
tests open many engines with `synchronous=FULL` — every commit an fsync — on
the same disk, which is exactly that pressure.

**This is a defect in the test, not in the heartbeat.** The behaviour under test
is "the beat continues while a goal is held open", which has no one-second
deadline in it; the fixed window was an implementation convenience that
silently doubled as a timing assertion.

**Fix.** Sample until the second distinct value is *seen*, bounded by a 15s
deadline, instead of for a fixed 1s. Verified not to have weakened it: patching
`_HEARTBEAT_SECONDS` to 9999 — the broken design where the beat is tied to the
coordinator loop and never advances — still fails the test.

Worth stating plainly: this one exists **because** of the change that found it.
A sweep that only adds load without checking what the load breaks would have
shipped a coin-flip CI failure and blamed the next person's branch.

## Recorded asymmetry (not a defect)

**The plan heartbeat renews an already-expired lease; the goal heartbeat
refuses to.**

```sql
-- plans
UPDATE plans SET lease_expires_at = :now_epoch + lease_seconds
WHERE id = :plan_id AND claimed_by = :worker_id

-- goal_leases: the extra guard
  AND lease_expires_at >= :now_epoch
```

Proven: a holder whose lease expired 999s ago still renewed it successfully
(`RESURRECTED` in the probe). The goal lease refuses the same move
(`test_heartbeat_after_expiry_returns_false_without_extending_lease`).

**Why this is not filed as a defect.** Mutual exclusion is never violated by it
— once another worker has claimed, the displaced holder's heartbeat is refused,
which is proven above. The behaviour only differs while *nobody else has taken
the plan*, and there the resurrection is arguably the better answer: the worker
is demonstrably alive, since it just heartbeated. No reproduction shows harm, so
it is recorded, not fixed.

**Related, and also not a defect:** `PlanRepository.heartbeat` returns `None`
while `GoalLeaseRepository.heartbeat` returns `bool`, so a displaced worker is
never *told* it lost the plan. The guard is elsewhere and it is real — the
version CAS in `save` (`rowcount == 0` -> `StaleVersionError`) refuses the write
a displaced worker would go on to attempt. Fencing happens at the write, not at
the heartbeat.

## F8 — the container leak tests fail on someone else's orphan, forever

**Severity:** CI reliability; a false failure that accuses the wrong code.
**Status:** fixed.

`test_no_container_survives_the_run` and
`test_a_small_startup_budget_does_not_abort_the_daemon_call` both ended with:

```python
listing = subprocess.run([binary, "ps", "-a", "--format", "{{.Names}}"], ...).stdout
assert "aipom-acceptance-" not in listing
```

That asserts the **whole machine** has no acceptance container — not that *this
run* cleaned up after itself. Any orphan from any source therefore fails them,
and keeps failing them, until someone prunes by hand. The failure reads as
"teardown is broken", which is precisely what is not wrong.

**Proof, and it is unusually complete.** The test failed in runs 1 and 2 of a
three-run flake check. Inspecting the machine between runs found exactly one
orphan:

```
aipom-acceptance-2c3d4265c45d   Exited (137) 9 minutes ago   alpine:3.20
```

It was removed by hand partway through run 3 — and **run 3 passed**. Cause,
removal, and recovery all observed.

**Where the orphan came from, and why that part is a retraction.** `Exited
(137)` is SIGKILL. It was left by an *earlier full-suite run of mine that hit a
10-minute command timeout and was killed mid-test* — `ContainerEnvironment`'s
teardown is a `finally`, and no `finally` runs through a `SIGKILL`. So the leak
was an artifact of how the audit was being run, not a product defect. The
adapter's teardown is careful and correct: the `finally` is armed **before**
`_start` precisely because a client-side timeout can still leave a
daemon-side container, it uses `rm -f`, it is bounded by
`_TEARDOWN_TIMEOUT_SECONDS`, and it swallows its own exceptions so it cannot
replace a real verdict. Nothing in it needed changing.

**Fix.** A `no_new_containers` fixture snapshots the acceptance containers
before the test and asserts no **new** survivor after. Same real assertion —
this run removed what it created — without the accidental claim about the
machine. Verified capable of failing: disabling `_teardown` makes all four
leak-checked cases fail, naming the leaked container.

What was **ruled out** along the way: cross-file interference from
`test_container_environment_failures.py`, the only other file touching
`ContainerEnvironment` — its cases use a missing binary, a refusing daemon and a
nonsense repo, so it never starts a real container. Docker and podman also
cannot see each other's containers, so the two parametrizations cannot collide.

## Retracted

Three, all caught by the rule that a measurement is not yet a finding.

1. **"16 workers can only claim 1 of 8 available plans — the claim path
   collapses under contention."** The measurement was real: 15 workers got
   `None` with 7 plans apparently free. The cause was the fixture. `plans` has
   `uq_plans_project_id`, a UNIQUE partial index enforcing *one project owns
   exactly one plan*, and the seed used `INSERT OR IGNORE` with a shared
   `project_id` — so 7 of the 8 inserts were silently dropped and there was only
   ever one plan to claim. With distinct projects: 8 workers, 8 distinct plans,
   zero duplicates. **The domain invariant is enforced at the database level**,
   which is the real finding hiding inside the false one.

2. **"`heartbeat` returning `None` means a displaced worker can write stale
   state."** It cannot; the version CAS refuses it. See above.

3. **"`ContainerEnvironment` leaks containers."** A real orphan was found on the
   machine, which is a real observation. But it was `Exited (137)` — SIGKILL —
   left by an audit run of mine that was killed by a command timeout, and no
   `finally` survives a kill. The adapter's teardown is correct on every path
   Python can observe (see F8). The *test* that reported it was wrong; the code
   it accused was not.

Both have the same shape as sweep 1's `PRAGMA foreign_keys` retraction and the
P8.6 Task 1 retraction before it: a true observation, an inference that did not
follow from it, and a cheap check that settles it.

## Scope note

None of this claims multi-worker execution is supported — it is not, and
`ROADMAP.md` defers it explicitly. The lease exists so that **a dead worker's
plan is recoverable by exactly one survivor**, and that is the property proven
here.

## Still unswept

- The frontend's error and stale-data states beyond the toast path fixed in
  sweep 1.
- **The reasoner tool-call surface against hostile or malformed model output.**
  It was *read* during this sweep and not exercised, so nothing about it is
  claimed either way. What reading found is a surface that already anticipates
  the obvious attacks: `execute_tool_call` answers an unknown tool name with a
  structured error instead of raising, and `_validate_submission` re-validates
  every submitted payload through Pydantic and converts a schema-shaped-but-wrong
  value into a transient `ReasonerUnavailable` rather than an unhandled
  exception — with a comment naming the real model that produced one. That is
  encouraging and it is not evidence. Proving it needs adversarial runs through
  `tests/fakes_llm.py::FakeLLMClient`, which is the next sweep's job. One thing
  to look at first: `execute_tool_call` catches `Exception` and returns
  `str(exc)` to the model, which is a blanket handler of the kind the API layer
  deliberately refuses, and it puts internal error text into a transcript sent
  to the provider.
- Remaining doc/code drift outside the files touched by these two sweeps.
