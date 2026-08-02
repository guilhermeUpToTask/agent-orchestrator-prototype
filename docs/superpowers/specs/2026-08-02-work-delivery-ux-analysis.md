# Delivering the work: options analysis for the hand-off UX

**Status:** analysis, not a decision. No implementation implied.
**Date:** 2026-08-02
**Question:** once goals and tasks have produced verified code, what is the best
way to put it in the developer's hands — a downloadable artifact, a `.zip`, a
live state, a pull request, or something else?

---

## 1. The finding that reframes the question

**"Delivery" is not one problem here. It is three, and which one you have is
decided by a field most operators set once and forget: `ProjectDefinition.repo_url`.**

`ProjectWorkspaceResolver.repository_path_for`
(`backend/agent_orchestrator/infra/git/project_workspace.py:101`) resolves three
topologies:

| # | `repo_url` | Where the work lands | The delivery problem |
|---|---|---|---|
| **A** | a local path or `file://` | **The developer's own repository** | None, technically. `cycle/<id>` is already in their checkout. This is a *discovery and review* problem. |
| **B** | `https://` or `ssh://` | `$ORCHESTRATOR_HOME/projects/<id>/repos/<sha256[:16]>` — a clone the orchestrator owns | **A real transport problem.** The branch exists only inside a hash-named directory the developer has never heard of. |
| **C** | unset | `$ORCHESTRATOR_HOME/projects/<id>/repo`, auto-seeded | Mostly moot — a demo repo nobody wants the code from. |

This matters because **the product currently gives one answer to all three.**
When no disposition is recorded, `CycleEvidenceSummary.tsx:93` tells the operator:

> push `refs/heads/cycle/<id>` and open a pull request into the repository's
> default branch

In topology **A** that is correct and nearly free. In topology **B** it is
*advice the developer cannot follow*: the branch is not in their checkout, and
nothing on the delivery surface tells them where it actually is. (`resolved_path`
is served by `GET /api/projects/{id}/readiness` — `reference.py:386` — so the
information exists; it is simply nowhere near the moment it is needed.)

**Any option below that ignores topology B is a partial answer.** That, more
than the choice between zip and bundle, is the thing to fix.

---

## 2. What exists today

Worth being precise, because most of the delivery machinery is already built and
the gap is narrower than it looks.

- **The git ladder is the real deliverable.** `task/<id>/<run>` → `goal/<id>` →
  `cycle/<id>`, promoted only when independently verified, with the default
  branch never written by plan work.
- **A publication gate** records one `OutputDisposition` — `open_pr | merge |
  retain_branch | discard` (`planning_artifacts.py:34`). A non-discard requires
  an `output_reference`, which is **free text the human types**
  (`planner_orchestrator.py:1182`).
- **`open_pr` and `merge` do not open or merge anything.** They record that a
  human did. There is no authenticated forge port; the roadmap has it in Phase 8.
- **Evidence is already first class.** `GET /api/plans/{id}/cycles/{cid}/evidence`
  serves accepted evidence per task (exact command, exit code, candidate and test
  commit SHAs), protected scope, promotion refs with merge SHAs, rejected and
  superseded counts, and the recorded disposition.
- **The UI renders that** in cycle history (`CycleEvidenceSummary.tsx`).

So the orchestrator can already *prove* what it did. What it cannot do is *hand
it over*, and it does not help the developer look at it.

---

## 3. Requirements

Derived from the product's own constraints and from what the evidence says about
reviewing agent output. Ordered by how much they should bind the decision.

**R1 — The default branch stays untouched, and delivery never violates that.**
Non-negotiable; it is the guarantee the whole branch ladder exists to make. Any
delivery mechanism that writes to `main`, or that pushes anywhere without an
explicit human act, is out.

**R2 — Review burden is the actual bottleneck, so delivery must reduce it.**
This is the strongest external signal. Sonar's 2026 survey of 1,100+ developers
found **96% do not fully trust AI-generated code, yet only 48% always check it**,
and developers spend roughly **24% of the work week** verifying and fixing it;
**more than a third say reviewing AI code takes more effort than reviewing a
human's.** A delivery format that dumps a large undifferentiated diff makes the
one expensive step more expensive. Classic review research points the same way:
defect detection falls from ~87% on changes under 100 lines to ~28% over 1,000,
with effectiveness dropping off past 200–400 lines.

**R3 — Evidence must travel with the code.** This is the product's actual
differentiator and the direct answer to R2's trust gap. A reviewer who can see
"this exact command exited 0 against candidate `4620490d`, and the test was
authored in a separate commit that was RED first" reviews differently from one
handed an anonymous diff. Delivery that drops the evidence throws away the
project's best argument.

**R4 — Local-first, no forge credentials assumed.** No GitHub token, no network,
no account. Anything requiring authenticated forge access is an *optional
enhancement*, never the primary path.

**R5 — The developer's own tools must work.** They already have an IDE, a
difftool, and git. Delivery should feed those, not replace them. A mediocre
in-browser diff viewer competing with their editor is a losing trade.

**R6 — Reversible and inspectable before acceptance.** The agent is unsandboxed
and its output is a *candidate*. The developer must be able to look before
anything touches their working tree, and walk away leaving no trace.

**R7 — It must work in all three topologies**, or say plainly which one it is
for.

---

## 4. The options

### Option 1 — Git branch in place (what happens today)

The work is already on `cycle/<id>`; the developer checks it out.

- **R1** ✅ **R3** ~ (evidence is in the app, not the branch) **R4** ✅ **R5** ✅ **R6** ✅
- **R2** ~ — one branch for a whole cycle is one big diff.
- **R7** ❌ — **only works in topology A.**

**Verdict: correct foundation, wrong presentation.** In topology A the delivery
is already done and the product simply does not say so — the developer is told to
push and open a PR when `git diff main..cycle/<id>` is all they needed. In
topology B the same sentence is a dead end.

### Option 2 — `.zip` of the resulting tree

- **R1** ✅ **R4** ✅ **R7** ✅
- **R2** ❌ — a zip has no diff. The reviewer must reconstruct what changed by
  comparing trees by hand. This actively *increases* the expensive step.
- **R3** ❌ — no commits, no authorship, no messages, no SHAs; the evidence
  references commit SHAs that a zip does not contain.
- **R5** ❌ — throws away git.
- **R6** ~ — safe, but only because it is inert.

**Verdict: reject.** A zip discards history, provenance and reviewability to
solve a transport problem that git already solves better. The one thing it is
genuinely good for — handing code to someone with no git at all — is not this
audience. This is the weakest of the options the question raised, and it is worth
saying so plainly rather than shipping it because it sounds simple.

### Option 3 — `git bundle` export

One binary file containing the cycle's commits and their ancestry, produced by
`git bundle create out.bundle main..cycle/<id>`. The receiver runs
`git fetch out.bundle cycle/<id>` and gets a real branch.

- **R1** ✅ **R3** ✅ (real commits, real SHAs — the evidence's references
  resolve) **R4** ✅ **R5** ✅ **R6** ✅ **R7** ✅ — *this is the one that fixes
  topology B*
- **R2** ~ — same one-big-diff issue as Option 1, but at least it is a diff.

Bundles are the standard answer for exactly this shape: one file regardless of
commit count, compressed, and — the property that matters — **it carries parent
links, so the receiver can see what base it was prepared against and rebase
correctly.** Simon Tatham's survey of forge-free contribution ranks an
incremental bundle second only to "here is a repo URL and a branch name",
specifically because it is one file and preserves that ancestry.

**Verdict: the right portable artifact, and strictly better than zip.** It is
also the smallest thing that unblocks topology B.

### Option 4 — `git format-patch` series

- Same benefits as a bundle for provenance, but **multiple files to herd**, text
  that email clients corrupt, no record of the base commit (so conflicts are more
  likely), and `git am` handles conflicts worse than rebase.

**Verdict: strictly dominated by the bundle** for this use case. Worth supporting
only if someone is doing mailing-list contribution, which is not this audience.

### Option 5 — Real pull request (authenticated forge)

The industry default: agents "turn issues into pull requests", and the
interaction pattern has become task-in → PR-out. It gets code review, CI, and
branch protection for free.

- **R2** ✅✅ — a forge diff viewer is the best review surface that exists, and
  it is one the developer already knows.
- **R3** ✅ — evidence can be rendered into the PR body.
- **R1** ✅ (PRs never write the default branch) **R5** ✅
- **R4** ❌ — needs credentials and network.
- **R6** ~ — pushing is externally visible and not silently reversible.
- **R7** — needs a remote; awkward for a purely local repo.

**Verdict: the best experience, and correctly deferred.** It cannot be the
*primary* path for a local-first tool (R4), but it should be the *preferred*
path when credentials exist. Note the honest constraint: today `open_pr` is a
recorded claim, and the UI's manual instructions are the real implementation.

### Option 6 — "Live state" (running preview of the result)

A live environment where the changed code runs and can be exercised.

- **R2** ~ — genuinely valuable for some changes (UI work especially): seeing it
  run answers questions a diff cannot.
- **R4** ❌❌ — requires knowing how to build and run an arbitrary project.
- **R6** ❌ — running unreviewed agent output, unsandboxed, as the user, is the
  exact risk `SECURITY.md` warns about. A preview is *executing the thing you
  have not yet reviewed*.
- Cost is very high: build detection, dependency install, port allocation,
  lifecycle, teardown.

**Verdict: reject for now, and not merely on cost.** For an unsandboxed
local-first tool, "run it before you look at it" inverts the safety model. The
narrow, honest version of this already exists and is worth surfacing better: the
orchestrator *did* run the verification command, and the evidence says what it
was and what it returned. Revisit only with preview evidence of demand, and only
behind a sandbox.

### Option 7 — In-app review surface (diff per goal/task, evidence attached)

Render the change in the console: per goal, per task, per verification stage,
with the accepted evidence next to the hunks it covers, and links out to the
developer's own tools.

- **R2** ✅✅ — this is the only option that attacks review burden *structurally*.
  A cycle branch is one big diff; but the orchestrator knows the change's
  internal structure — which task produced which commit, which stage was
  test-authoring vs implementation, what the protected scope was. Splitting a
  700-line cycle into six task-sized reviews with evidence attached is precisely
  the 200–400-line reviewable unit the research points at, and the orchestrator
  is the only component that *can* split it, because it recorded the boundaries.
- **R3** ✅✅ — evidence and diff in the same place.
- **R1 R4 R6** ✅ **R7** ✅ — reads the repo the resolver already knows.
- **R5** ~ — must complement the IDE, not compete. Pair every view with the
  exact command to open the same thing locally.

**Verdict: the highest-value work, and the most product-specific.** Everyone can
show a diff. Only this system can show *a diff whose test was proven RED before
it went GREEN, whose scope was enforced, and whose promotion SHA is recorded*.

---

## 5. Recommendation

Sequenced by value per unit of work. Nothing here needs a domain un-freeze:
`OutputDisposition` already has the right four values, and all of this is read
models, adapters and UI.

### First — make the existing delivery findable, and fix topology B

The cheapest real win, because the code is already delivered and the product
hides it.

At the publication gate and in cycle history, replace the single generic sentence
with what is actually true for *this* project:

- **Topology A:** the resolved repository path plus copy-paste commands —
  `git -C <path> diff <default>..cycle/<id>`,
  `git -C <path> switch cycle/<id>`,
  `git -C <path> difftool <default>..cycle/<id>`.
- **Topology B:** say plainly that the work is in the orchestrator's clone, show
  the resolved path, and offer the two ways out: add it as a remote
  (`git remote add orchestrator <path> && git fetch orchestrator cycle/<id>`) or
  download a bundle.
- **Topology C:** say it is a scratch repository, and that this run demonstrates
  the flow rather than producing code they want.

This is copy and one read model. It converts a dead end into a hand-off.

### Second — `GET …/cycles/{id}/bundle` (and keep zip off the menu)

A `git bundle` of `<default>..cycle/<id>`, served as a download, with the exact
`git fetch` line to apply it. This is the portable artifact the question was
reaching for, and it is the one format that keeps history, SHAs and the base
commit intact — so the evidence document's commit references still resolve on
the other side.

Ship it *with* an evidence file (the JSON the endpoint already serves) so the
two travel together.

### Third — the in-app review surface, scoped by task

Diff per goal and per task, evidence beside it, protected scope shown, and every
view paired with the local command that opens the same thing. Start read-only:
no accept/reject-hunk UI. The lesson from tools that went further is that
partial-acceptance UX is where the complexity lives — the most-requested Claude
Code features in this area are batch and hunk-level review, and they are hard
precisely because half-accepted agent output has no coherent verification story.
Here, half-accepting would invalidate the evidence that makes the change
trustworthy, which is a good reason to keep acceptance at the granularity the
orchestrator can actually verify.

### Fourth — forge publication behind the existing seam

When a token exists, `open_pr` should really open one, with the evidence rendered
into the body. Keep the manual path as the default and the fallback. This is
already Phase 8 and should stay there until preview evidence says operators want
it.

---

## 6. What not to build

- **`.zip` of the tree.** Loses history, provenance and reviewability; solves
  nothing git does not solve better for this audience.
- **`format-patch` series.** Dominated by the bundle.
- **Live preview environments.** Inverts the safety model of an unsandboxed tool
  and costs the most.
- **A general in-browser merge/conflict resolver.** Their editor is better at it.
- **Hunk-level accept/reject**, at least until the verification story for a
  partially accepted candidate is answered.

---

## 7. Open questions for the maintainer

1. **Which topology is the real target?** If most preview users will point at a
   local path (A), step one is nearly the whole job. If remote URLs (B) are
   expected, the bundle moves up.
2. **Is the cycle branch the unit of delivery, or the goal branch?** Per-goal
   delivery would fit the review-size research better, but the publication gate
   is currently per cycle.
3. **Should `output_reference` stay free text?** It is the one place a human
   asserts something the system cannot verify. A bundle download or a real PR
   would let it be a fact instead of a claim.

---

## Sources

- [Sonar — "State of Code" developer survey: the verification gap](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/)
- [Sonar — The current reality of AI coding](https://www.sonarsource.com/blog/state-of-code-developer-survey-report-the-current-reality-of-ai-coding/)
- [Simon Tatham — Git without a forge](https://www.chiark.greenend.org.uk/~sgtatham/quasiblog/git-no-forge/)
- [git-scm — Bundling](https://git-scm.com/book/en/v2/Git-Tools-Bundling) · [git-bundle docs](https://git-scm.com/docs/git-bundle)
- [Propel — The impact of PR size on code review quality](https://www.propelcode.ai/blog/pr-size-impact-code-review-quality-data-study)
- [cubic — Does PR size actually matter?](https://www.cubic.dev/blog/does-pr-size-actually-matter)
- [OpenHands — What are coding agents?](https://www.openhands.dev/blog/what-are-coding-agents)
- [Claude Code — batch diff review request](https://github.com/anthropics/claude-code/issues/31888) · [hunk-level review request](https://github.com/anthropics/claude-code/issues/42448)
- [How AI coding agents work in 2026: from autocomplete to autonomous pull requests](https://dev.to/dhruvjoshi9/how-ai-coding-agents-work-in-2026-from-autocomplete-to-autonomous-pull-requests-i3c)
