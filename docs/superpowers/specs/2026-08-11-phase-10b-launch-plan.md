# Phase 10B — the launch plan

**Date:** 2026-08-11
**Status:** plan only. Nothing has been published, no channel has been posted to,
and no account has been created.
**Depends on:** the rename
([scope](2026-08-11-phase-10b-rename-scope.md)), **executed 2026-08-15**. The
name is settled and is not a blocker.

ROADMAP asks for this *"written the way a marketing team that has shipped
developer tools would write it rather than the way engineers imagine marketing
works."* The one thing such a team would insist on, and which this project is
unusually well placed to deliver, is that **every claim in the launch is one a
sceptic can check.**

Which is why this plan opens by correcting the headline the roadmap proposed.

---

## 0. The headline claim — CORRECTED TWICE. Read this second correction first.

**Update, later on 2026-08-11: the section below was itself wrong, and the
original roadmap headline was right.**

It concluded that "the test proven RED" was unsupported. That was inferred from
the *exported evidence* — every task showing `exit_code: 0` — without checking
the execution path. The execution path does prove it:

- After the test-authoring stage the orchestrator **runs the checks**
  (`ExecutionHandler`, the `test_authoring` branch).
- `app/verification.py::baseline_outcome` computes the verdict and, for `tdd`
  and `executable_check`, **requires it to be red**. A green baseline raises
  `TaskFailed("test bundle did not establish a failing baseline")`, so a task
  cannot reach a frozen bundle without a proven failure first.
- The verdict, the commands and the exit codes were recorded as a
  `verification_baseline` planning artifact.

So the system had always proven RED and refused to continue without it. What was
missing was only that **the published evidence document did not say so** — and,
underneath that, that the artifact was unreachable: the store matches
`goal_id IS :goal_id`, so a query without a goal id selected the *plan-wide*
rows and returned nothing for a per-goal purpose.

Both are fixed. `GET …/cycles/{id}/evidence` now carries
`test_bundle.baseline` — verdict, commands, exit codes — beside the accepted
green, and `GET …/planning-artifacts` without a `goal_id` now spans every goal.
Locked by `test_the_evidence_says_the_tests_were_proven_failing_first` and
`test_the_baseline_and_the_pass_are_both_readable_from_one_document`.

**So the roadmap's short headline is available after all**, once a demo is
re-run so a published `evidence.json` carries the field:

> *the orchestrator records the boundary between the test proven RED and the
> implementation that made it GREEN.*

The lesson is the one this phase keeps re-learning, and it cuts both ways: an
absence in an export is not an absence in the system. I retracted a true claim
on incomplete evidence, which is the same error as asserting a false one.

The original analysis is kept below unedited, because the reasoning error is the
instructive part — the same treatment the `known-issues.md` failure-
classification retraction gets.

---

## 0b. The original (WRONG) analysis, kept for the record

ROADMAP proposes leading with:

> *the orchestrator records the boundary between the test proven RED and the
> implementation that made it GREEN, and nothing else does.*

**Half of that is not supported by the published evidence.** Checked against
`demos/static-site-v1/runs/20260810T164908Z-d098aece/evidence.json`:

| claim | status |
|---|---|
| tests are frozen before implementation | **provable** — `test_bundle.state: "frozen"`, `verification_strategy: "tdd"` |
| the boundary is two distinct commits | **provable** — `test_commit_sha` ≠ `candidate_commit_sha` on every task |
| the implementer did not edit the tests | **provable** — `protected_file_hashes`, SHA-256 per test file |
| the passing run is reproducible | **provable** — `exact_command`, `exit_code: 0`, `bounded_output_ref` |
| **the test was observed failing (RED) first** | **NOT in the evidence** |

All five tasks in the clean run carry `exit_code: 0` and
`rejected_evidence_count: 0`. There is no recorded failing verification. The
system has the *structure* that makes RED meaningful — frozen, hash-protected
tests written by a different agent in a different commit — but the published
artifacts do not contain a red run, so "proven RED" would be a claim a reader
could ask for and we could not produce.

Launching on an unfalsifiable claim would be the single most damaging thing this
project could do, because **verifiability is the entire pitch.** One person
asking "show me the red run" and getting a shrug undoes every other honest
number.

### The claim to lead with instead

> **The tests were frozen and hashed before the implementation existed. The code
> that made them pass is a different commit, by a different agent, that provably
> could not edit them. Here is the command, the exit code, and the hash of the
> output.**

Longer, and stronger, because every clause resolves to a field in a published
JSON file. It is also still unusual: the differentiator survives the correction.

### To earn the shorter claim

Record and export the RED run — the test-authoring stage's own verification,
showing the frozen tests failing against the pre-implementation tree. Most of
the machinery exists (`run_kind` is in the orchestration path;
`known-issues.md` notes it is not yet a dedicated ledger column). Until that
ships and a demo run publishes one, the long version is the honest one.

**This is a Phase 10 finding, not a marketing preference.** It goes on the board
as work, and if it lands before launch the headline gets shorter.

---

## 1. Positioning

**One sentence, for a developer to repeat after hearing once:**

> *It runs coding agents against your repo, and it won't merge anything the
> tests didn't prove.*

**The paragraph:**

> Praxis Orchestrator is a local-first orchestrator for coding agents. You give
> it a brief; it proposes a plan you approve, freezes the tests before any
> implementation exists, runs agents in isolated worktrees, and independently
> verifies every candidate before it moves up a branch. Nothing merges on an
> agent's say-so. Every run leaves a receipt: the commands, the commits, the
> exit codes.

**What we are NOT claiming**, and the discipline is deliberate:

- Not "AI writes your code" — nobody is short of that claim.
- Not autonomy. The pitch is the *gates*, not their absence.
- Not "parallel agents" as a headline. Parallelism is per-goal; task-level
  parallelism is on the rejected list. Leading with it would overstate.
- Not a benchmark. One demo on one repository is not a benchmark and will not be
  presented as one.

**Positioning against the field:** the crowded space is autonomy — agents that
do more with less supervision. This sits at the opposite end: a system whose
value is that it *refuses* work. That is a real position, and it is aimed at
people who have already been burned by the other one.

---

## 2. Audience and channels

**Who, specifically:** developers already running coding agents on their own
repositories who do not trust the output — and who therefore already review
every diff by hand. The product's job is to make that review smaller and
evidence-backed. People not yet using agents are the wrong audience; the pitch
requires having been disappointed already.

| channel | norms | what to post | risk |
|---|---|---|---|
| **Hacker News (Show HN)** | tolerates self-promotion in the right format; hostile to hype; rewards a working thing with numbers | the honest post: what it does, the 13m/$0.0134 run, the published evidence, and the limitations section | highest-variance. One vague claim gets dismantled in the top comment. Post only when the README is the landing page and the run is linkable |
| **r/ExperiencedDevs, r/devtools** | deeply anti-ad; fine with "I built this and here's what it doesn't do" | lead with the *limitations* and the retraction culture; that is the unusual part | ban risk if it reads as marketing. No links in the first paragraph |
| **Lobste.rs** | small, technical, low tolerance for shallow | the architecture: frozen contracts, protected scope, revision-bound evidence | needs an invite; do not treat as a launch channel |
| **The repo itself** | — | README as the sales page; it is the real landing page for a developer tool | none — do this first regardless |

**Sequencing between channels is deliberate:** repo → one Reddit post → Show HN.
Reddit first because it is the cheaper place to discover the objections you have
not thought of, and Show HN is the one you only get to do well once.

---

## 3. Assets

Almost all of these already exist, which is the unusual part.

| asset | state | work needed |
|---|---|---|
| Published run evidence — `demos/static-site-v1/runs/` (two runs, manifest, attempts, evidence, worker log) | **exists** | none; link it |
| Measurement write-up — `docs/history/analyses/2026-08-10-cycle-latency-second-measurement.md` | **exists** | none |
| Console screenshots — `docs/images/` (5) and `frontend/e2e/screenshots/` incl. light/dark | **exists** | pick 3 |
| Audit records — four Phase 10A sweeps with reproductions and retractions | **exists** | link from the README's trust section |
| README as a sales page | needs rewrite | the main writing job |
| Demo video (~2 min) from the `static-site-v1` run | not started | screen capture of a real run, no voice-over edits that hide waiting |
| Landing page | not started | only after the domain is registered; the README carries the launch if it slips |

**The evidence documents are the asset nobody else has.** Not because they are
impressive — because they include the failures. A published run where a goal
failed, and the record of a claim retracted before implementation (the P8.6 Task
1 retraction, and four more in Phase 10A) is more persuasive to this audience
than any success metric.

---

## 4. Proof over promises — the numbers we may use

Only these. Each is linkable.

- **5 of 5 goals, 13m 03s, $0.0134, 10 attempts, zero failures** — the clean run,
  `20260810T164908Z-d098aece`.
- **Against its own baseline: 61 minutes with 0 of 5 goals promoted.** The
  before-and-after is the honest number, and the "before" is ours.
- **1509 automated tests**, including orchestration run against both in-memory
  fakes and real SQLite.
- **Tier 0 is free and deterministic** — stub reasoner, dry-run runtime; a
  visitor can drive the whole lifecycle without an API key.

**Numbers we may not use:** anything per-hour or per-month extrapolated from one
run; any comparison to another tool we have not run; "X% faster" without naming
the baseline as our own broken run.

---

## 5. Metrics, and the stop-and-fix threshold

Decided now, before launching, as the roadmap requires.

| channel | success | what it means |
|---|---|---|
| Show HN | front page 4h+, ≥20 substantive comments | the positioning lands |
| Reddit | ≥10 comments, not downvoted below 60% | the framing is not reading as an ad |
| Repo | ≥30 stars week 1 | weak signal, tracked but not steered by |
| **Installs that reach a completed cycle** | ≥5 in week 1 | **the only metric that matters** |

**The stop-and-fix threshold — agreed in advance:**

> **If more than half of the people who install it cannot complete one cycle,
> stop marketing and fix the product.**

Rationale: every other number measures whether the message travelled. That one
measures whether the thing works for someone who is not us. It is also the
failure this project is most exposed to — every run to date has been on the
`praxis-dev` guest, by its author. The first-mile has never been walked by a
stranger.

Two supporting tripwires:
- **The same setup failure reported twice → stop and fix.** Twice is a pattern.
- **Any claim in the launch post shown to be unsupported → retract publicly, in
  the thread, same day.** This project's credibility is its retraction record;
  the launch inherits that standard or it inherits nothing.

---

## 6. The support path

*"A support channel with nobody in it is a maintenance cost — build it when the
first invitation goes out, not before."*

**At launch, exactly two:**

1. **GitHub Issues**, with three templates: bug, setup-failed, and *"the
   orchestrator accepted something it shouldn't have"* — the last is its own
   template because it is the highest-signal report this product can receive,
   and it should not arrive as a vague bug.
2. **GitHub Discussions**, one category, for "did I set this up right".

**No Discord, no Slack.** Both imply a response time that one maintainer cannot
hold, and both make the knowledge unsearchable.

**Response commitment, stated publicly in the README** so it is a promise that
can be kept: *first response within 48h on weekdays; setup failures prioritised
over feature requests.*

**Feedback loop:** every setup failure becomes a fixture or a readiness check —
that is already how `preflight.sh` and the readiness endpoint came to exist. Every
"accepted something it shouldn't have" becomes a regression test, per the repo's
existing rule. The loop is not new machinery; it is the discipline already in
`CLAUDE.md`, pointed at strangers instead of at ourselves.

---

## 7. Sequence

1. ~~Rename~~ — **landed 2026-08-15** ([scope](2026-08-11-phase-10b-rename-scope.md)). Register the name on PyPI, npm, GitHub and the domains.
2. README rewritten as the landing page, with the corrected claim from §0.
3. Issue/Discussion templates and the response commitment.
4. Re-run `static-site-v1` — this replaces the substituted container names in
   recorded evidence and refreshes the headline numbers at the same time. It
   needs no guest rebuild: the mark came from the acceptance-container **name
   prefix** in code, now `praxis-acceptance-`, and nothing in the package reads
   the hostname.
5. Demo video from that run.
6. Reddit post. Wait a week. Absorb the objections.
7. Show HN.
8. Landing page, if the domain is up; not a blocker.

**Not before step 1.** A launch under a name that has to change again is worse
than a launch a month later.

---

## Open items for the owner

- **Register the name** on PyPI, npm, the GitHub org and the two domains. The
  name itself is settled — see the rename scope's *Closed item*.
- **The RED-run gap in §0** — decide whether to build it before launch (shorter,
  stronger headline) or launch with the longer honest claim.
- **Who answers the issues**, and whether the 48h commitment is one you want to
  make publicly. If not, say nothing rather than promising and missing.
