# Reporting a preview run

You agreed to try this and tell us what happened. This is what to send.

**It should take about ten minutes.** Most of it is generated — you run two
commands and attach the output. The part only you can write is the last
section, and it is the part that decides what gets built next.

Send a report **whether or not the run worked**. A clean run that took you forty
minutes to set up is as useful as a crash.

---

## Part 1 — the facts (generated, don't retype)

```bash
# what you installed — this is what makes two reports comparable
orchestrate version

# the run itself: plan, planning operations, runs, attempts, telemetry, events
python3 -m agent_orchestrator.infra.cli.main config list
python3 backend/scripts/export_plan_runs.py --plan-id "$PLAN" --pretty --output run.json
```

If you used a fixture, add its independent check — it re-asks git whether the
SHAs the API served are real:

```bash
python3 fixtures/first-cycle-v1/scripts/verify_run.py --plan "$PLAN" --repo "$REPO" --tier 0
```

Attach `run.json`. **Read it before you send it** — it carries your brief, chat
history, file paths and commands, which is the point, but a brief can contain
something you would rather not share. It never carries credentials: keys are
envelope-encrypted and referenced by URI, so the risk is your content, not its
secrets. See [evidence.md](evidence.md) for the sanitized-bundle option.

Then tell us in one line each:

- **Which tier** — Tier 0 (stub + dry-run, no API key) or Tier 1 (real reasoner
  + real agent runtime). Never mixed.
- **Which fixture, or your own project** — and if your own: roughly what kind
  (web frontend, CLI, API, library, full-stack), and how large.
- **Repository binding** — a local path, a remote URL, or nothing (scratch).

---

## Part 2 — the eight things we are measuring

One or two sentences each. **Skip any that did not come up** — a blank is data,
not a gap. Numbers beat adjectives wherever you have one.

**1. Install and time to first cycle.**
How long from "nothing installed" to a completed cycle? Where did the clock
actually go?

**2. Setup, runtime, or repository failures.**
Anything that stopped you. Include what you tried before it worked, and whether
[troubleshooting.md](troubleshooting.md) covered it.

**3. States or actions that were unclear.**
A moment you looked at the console and could not tell what the system was doing,
or what you were supposed to do next. Name the screen.

**4. Recovery.**
Something failed — a task, a provider, a contract. Could you get the run moving
again without restarting it? What did you try?

**5. Capacity and cost.**
Tier 1 only. Rate limits, quota waits, concurrency refusals. Roughly what did
the run cost, and did that match what you expected before starting?

**6. Evidence trust.**
You were handed exact commands, exit codes and commit SHAs. Did you believe
them? Did you check any yourself? Did checking change your mind?

**7. Git output.**
Could you find the code the run produced, and get it where you wanted it? If
you used a remote-URL binding, say specifically whether you got the branch into
your own repository.

**8. Missing controls.**
Something you wanted to do to a running plan and could not.

---

## Part 3 — the one that decides what we build

> **What is the single thing that would most change whether you keep using
> this?**

Answer in your own words. Do not try to be constructive or propose a feature —
describe the friction and let us map it.

We map it against a list we wrote down *before* asking, so the answer picks the
work rather than confirming a plan we already had
([ROADMAP.md](../../ROADMAP.md), Phase 7):

| If your answer is about… | It promotes |
|---|---|
| finding or extracting your code | the repository-choice wizard, then bundle export |
| a diff too large to review | the per-goal review surface |
| tests passing while the app was broken | the cycle acceptance run |
| wanting a pull request like everything else | authenticated forge publication |
| being stuck without knowing why | the advisory observer agent |

If your answer fits none of these, that is the **most** useful report we can
get, and we want it in your words rather than squeezed into a row above.

---

## Ground rules that make reports comparable

- **Change one variable per run.** Two runs that differ in model *and* project
  cannot be compared, and a series of them tells us nothing.
- **Start with a canonical fixture before your own repository.** `first-cycle-v1`
  is one command and gives us a baseline your later runs are measured against.
- **Point Tier 1 at a disposable repository first.** Agent runtimes execute
  unsandboxed, as your user — [SECURITY.md](../../SECURITY.md) is short and
  worth the two minutes before you aim this at something you care about.
