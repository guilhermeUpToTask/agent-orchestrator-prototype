# History — the paper trail

*Nothing in this folder describes the current system.* These are archived planning documents, debugging analyses, and the pre-refactor documentation set — kept verbatim so decisions stay traceable, old designs stay recoverable, and the project's development story stays tellable. Files are prefixed with their date; planning docs also carry the model that produced them.

## planning/ — the plans, in order

| Date | Document | What it was | Outcome |
|---|---|---|---|
| 2026-06-12 | [code-review remediation M1–M5](planning/2026-06-12-code-review-remediation-m1-m5-fable-5.md) | Fix the old backend's verified delivery/topology/state-writer defects (consumer groups, PEL recovery, embedded coordinators, single-writer tasks, SSE bridge, mypy ratchet) | Executed against the **old** backend (`review M1–M5` commits); the SSE broker + single-writer lessons carried into the new system |
| 2026-06-13 | [API stability + frontend recovery](planning/2026-06-13-api-stability-frontend-recovery-opus-4-8.md) | The missing architecture-run endpoints, the `.gitignore` that swallowed `frontend/src/lib/`, provider tool-use error guards | Executed on the old backend |
| 2026-06-13 | [architecture-session hardening](planning/2026-06-13-architecture-session-hardening-opus-4-8.md) | The terminal-tool name-mismatch root cause (`submit_architecture` vs `submit_final_roadmap`), auto-finalize, cancel, typed roadmap | Executed on the old backend; the terminal-tool *flag* idea lives on in `src/infra/reasoner/runtime/tools.py` |
| 2026-06-14 | [architecture-phase fix, backend-first](planning/2026-06-14-architecture-phase-fix-opus-4-8.md) | Observability/timeouts for planner runs, model/thinking compatibility, gate desync, auto-start | Executed on the old backend |
| 2026-06-15 | [Playwright E2E plan (deferred)](planning/2026-06-15-playwright-e2e-plan-deferred.md) | Browser E2E for the old architecture-phase flow | Never implemented; targets dead endpoints — the environment lessons feed ROADMAP #19 |
| 2026-07-02 | [**Master roadmap FINAL**](planning/2026-07-02-master-roadmap-final-fable-5.md) | The gap-closed, r2-audited plan for the full refactor: the nine-phase machine, driver model, freeze, integration, launch | **The refactor's blueprint.** Phases 0–2 + slices of 3–4 executed; leftovers → [ROADMAP.md](../../ROADMAP.md); decisions → the [decision log](../decisions/decision-log.md) |
| 2026-07-03 | [working prototype: real reasoner + frontend re-point](planning/2026-07-03-working-prototype-reasoner-frontend-fable-5.md) | The 9-stage plan that built the conversational reasoner (two-method port, tool loop, catalog resolution) and re-pointed the frontend | Executed — this is how the current reasoner + frontend came to be |
| 2026-07-06 | [orchestrator evolution plan](planning/2026-07-06-orchestrator-evolution-plan-fable-5.md) | Full-codebase archaeology with `file:line` evidence: control-flow map, load-bearing hacks, stress tests, three futures, phased fix plan | Findings → [known-issues.md](../architecture/known-issues.md); plan → [ROADMAP.md](../../ROADMAP.md) Now/Next/Then |

## analyses/ — raw debugging sessions (old backend)

Operator-driven walkthroughs that found the bugs the June plans then fixed. Kept raw — they show what actually broke, verbatim:

- [2026-06-08 — API endpoint walkthrough](analyses/2026-06-08-api-endpoint-walkthrough.md): spec router 500s, discovery-without-start timeouts, API-layer/CLI boundary violation.
- [2026-06-10 — spec API + discovery fixes](analyses/2026-06-10-spec-api-and-discovery-fixes.md): the four fixes (attribute access, invocation, concurrent-discovery guard, container-in-app violation).
- [2026-06-13 — planner provider + frontend gaps](analyses/2026-06-13-planner-provider-and-frontend-gaps.md): the tool-use 404, the `model_dump` crash mid-discovery, the approve-architecture 409 dead-end, and the missing project-management UI list.

## pre-refactor/ — the old documentation set, verbatim

The docs that described the system before the 2026-07 refactor — **none of it is true of the current code**: [README](pre-refactor/README.md) · [architecture](pre-refactor/architecture.md) · [orchestration authority matrix](pre-refactor/orchestration-authority-matrix.md) · [roadmap](pre-refactor/roadmap.md). The distilled, organized version — what existed, why it was shelved, how it could return — is [../legacy/pre-refactor-backend.md](../legacy/pre-refactor-backend.md).

## Conventions

- Files are immutable once archived; corrections happen in living docs, not here.
- New superseded plans: `planning/YYYY-MM-DD-<slug>-<model>.md`, plus a row in the table above.
