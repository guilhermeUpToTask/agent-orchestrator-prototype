# 2026-07-20 — API-driven walkthrough findings

Walkthrough of the **current plan `fc5fa4c3`** (a `restful server` project, cyclic,
`replan` intent at revision 1 over a prior 4-goal cycle) driven and monitored
**only through the FastAPI layer** (`agent_runner.mode=real`, `reasoner.mode=llm`
on OpenRouter nemotron-free). Stack started via `backend/scripts/dev.sh start`.

Method: `GET /api/plans/{id}` (status/activity/legal_actions/block), the worker
structlog, and `GET /api/plans/{id}/attempts/{aid}/log` for live output. Control
via `POST /api/plans/{id}/intent/approve`, `/pause`.

---

## Finding #1 — unpromotable goal hot-loops the worker at 1 Hz  ✅ FIXED

**Trigger:** `POST /api/plans/fc5fa4c3/intent/approve` (approve the replan intent)
→ plan went `running` / `cycle_architecture`.

**Symptom:** the worker entered a 1 Hz `worker.tick_failed` storm:

```
src.app.ports.TaskFailed: goal cannot merge without accepted task evidence
  run_worker_forever (main.py:81) → worker_tick (run_worker.py:141)
  → drive_plan (:96) → _advance_with_heartbeats (:61)
2026-07-20 20:06:41 [error] worker.tick_failed worker_id=worker-1   (repeats every second)
```

**Root cause:** `ExecutionHandler.handle()` calls `_reserve_goal_promotion` for a
`(goal, None)` navigation result (all tasks terminal, none failed → close the
goal) **inside txn1 but outside the `try/except TaskFailed` that guards the
agent-run path** (`execution_handler.py:141` vs the handler at `:206`). The prior
cycle contains a goal (`1cba567d…`) whose task (`99446048…`) is `DONE` but has no
`verification_evidence` (a legacy/replan artifact), so the promotion guard
(`_reserve_goal_promotion`, `:706`) raises `TaskFailed`. With no handler it
escapes to `run_worker_forever`, is logged as `tick_failed`, and is re-dispatched
every tick — the "poisoned-plan starvation" pattern, but as a hot spin (the
guard raises before any backoff/block is armed). No LLM quota is burned (the
failure is local logic), but the worker never makes progress on any plan.

**Fix** (`fix(execution): block instead of hot-loop on an unpromotable goal`,
commit on this branch): catch the `TaskFailed` at the promotion call site and
open a structured `execution_failure` `PlanBlock` (mirroring
`_pause_on_failed_goal`) pointing at the first non-DONE / evidence-less task,
with `legal_resolutions: [retry_stage, edit_task, start_replan]`.

**Verified live:** after restarting the stack on the fix, `fc5fa4c3` went to
`status=blocked`, `activity=blocked:implementation`, block
`{kind: execution_failure, goal_id: 1cba567d…, task_id: 99446048…}`, and the
post-fix worker log shows **0** `tick_failed`. ruff + mypy clean.

---

## Finding #2 — cannot pause a RUNNING cyclic plan; legal_actions lies  ⚠ OPEN (needs domain un-freeze)

**Trigger:** while the plan was `running` (mid-storm), `GET /api/plans/{id}`
reported `legal_actions` including `pause`. `POST /api/plans/{id}/pause` returned:

```
HTTP 422  INVALID_TRANSITION
"Plan 'fc5fa4c3…' cannot transition from running to paused."
```

**Root cause:** `Plan.pause()` / `Plan.request_pause()`
(`planner_orchestrator.py:264,284`) guard on the **legacy** `self.phase not in
WORKER_CLAIMABLE_PHASES`, but `legal_actions` is derived from the **cyclic**
`status` + open artifacts. A cyclic plan can be `status=RUNNING` with an active
cycle while its legacy `phase` projection is not in the claimable set → the
command the API advertises as legal is rejected by the domain guard. So a
runaway/`running` cyclic plan cannot be paused through the documented control.

**Recommended fix (NOT applied — frozen domain):** the pause guard should key on
the cyclic authority (`status == RUNNING and active_cycle is not None`) rather
than the legacy `phase`, so `legal_actions` and the transition guard agree. This
mutates the frozen `Plan` aggregate and therefore requires a deliberate,
decision-logged **un-freeze** (a human gate) — filed here rather than changed
silently. Until then, operators must resolve via a block's `legal_resolutions`
or stop the worker, not `pause`.

---

## Finding #3 — navigation eligibility ≠ promotion eligibility  ⚠ OPEN (latent, surfaced by codex review)

Independent codex analysis of the storm confirmed both root causes and the fix's
transaction safety (`_reserve_goal_promotion` is validation-first — it raises
*before* `reserve_promotion`/`bump_version`/`save`, so the same-txn block sees an
unmutated plan; idempotency holds because the block makes the plan non-claimable,
so it is never re-dispatched — live-confirmed by 0 post-fix `tick_failed`).

It also surfaced the deeper semantic mismatch the #1 fix does not resolve:
`peek_next()` returns `(goal, None)` ("promotable") based only on tasks being
**terminal and none failed**, while `_reserve_goal_promotion` additionally
requires every task to be `DONE` **with evidence**. A `DONE`-but-evidence-less
task (or a skipped/cancelled terminal task) therefore produces a *permanently*
unpromotable navigation result. The #1 fix converts the resulting storm into a
block, but the two predicates should be reconciled — navigation should return a
distinct blocked/invalid state, or share one canonical `can_promote` predicate
with the reservation. **Follow-up, not yet done.**

Related open question for the next walkthrough layer: verify the block's
advertised `legal_resolutions` (`retry_stage`/`edit_task`/`start_replan`) can
*actually* repair a `DONE`-but-evidence-less task — otherwise the block is only
nominally recoverable.

## Finding #4 — block advertises a resolution that can't run (`retry` on a skipped task)  ✅ FIXED

Confirming codex's "nominally recoverable" warning against the live plan:
`POST /api/plans/{id}/retry {goal_id, task_id}` on the block returned

```
HTTP 422  INVALID_TRANSITION
"Task '99446048…' cannot transition from skipped to pending."
```

The offending task is **`skipped`** (a terminal-but-not-DONE state — exactly the
case Finding #3 warned about), not FAILED. `Task.retry()` only allows
`FAILED → pending`, so the `retry_stage` resolution the block advertised is
rejected the moment an operator invokes it — a nominal-only recovery action.

**Fix** (this branch): `_block_on_unpromotable_goal` now advertises
`legal_resolutions` honestly — `retry_stage` only when the offending task is
`FAILED`; otherwise just `edit_task` / `start_replan`. Blocks no longer offer a
resolution the API will reject. (The deeper cure remains Finding #3 — navigation
should not select a goal with a `skipped`/evidence-less task as promotable in the
first place.)

## Finding #5 — `start_replan` also rejected; `fc5fa4c3` is API-unrecoverable (wedged)  ⚠ OPEN (human gate)

`POST /api/plans/{id}/replan` on the block → `422 INVALID_TRANSITION`
"cannot transition from replanning to replanning" (the legacy `phase` is already
`replanning` from the original replan that built this cycle). Combined with
Finding #4 (`retry` rejected on the `skipped` task), **two of the block's three
advertised resolutions fail** — the same legacy-phase-vs-cyclic-status mismatch
as Finding #2, now in the recovery-command guards. `fc5fa4c3` cannot be driven
forward through the FastAPI layer: it is wedged by a legacy `skipped`-task
artifact, and every non-`edit` recovery is rejected by a stale-phase guard.

**Recovering it requires a human gate** — either the domain un-freeze that
Findings #2/#4/#5 all point to (guard on cyclic `status`/`active_cycle`, not
legacy `phase`), or direct DB state surgery. Neither is takeable autonomously.

**Loop pivot (recorded):** to honor "keep looping until the plan cycle is
finished or the OpenRouter rate limit is exhausted," the walkthrough continues on
a **fresh plan under the same project** (`ba5c0163…`, "restful server"), driving
the full cyclic lifecycle via the API — which exercises the OpenRouter reasoner
(the actual rate-limit stop condition) and can reach completion. `fc5fa4c3`
remains parked pending the un-freeze decision above.

## Finding #6 — reasoner 500s on an intent-with-questions from the model  🔧 FIX DISPATCHED

Driving discovery on a fresh plan (`ae47ee19`, project `walkthrough-healthz`) the
free OpenRouter nemotron model submitted an `IntentCandidate` that still had
`unresolved_questions`. `OpenAIReasoner.converse` (`openai_reasoner.py:430`)
raises a bare `ValueError("submitted intent cannot retain unresolved questions")`,
which is **unhandled → `POST /api/plans` returns 500 Internal Server Error**.
Model flakiness (a malformed intent shape) exposed a system defect (an unmapped
exception reaching the client as a 500; the conversation should degrade
gracefully, not crash).

**Fix (dispatched to codex, `--sandbox danger-full-access`, own branch):** treat a
submitted-intent-with-unresolved-questions as a **question turn** — return a
`ReasonerReply` carrying the questions with `intent=None` so discovery stays open
for another turn, instead of raising. (The plan is left recoverable at
`activity=intent_discovery`, `legal_actions=[start_intent]`.)

## Environment note (not a plan defect)

Worker boot warns `worker.dependency_missing binary=gemini` — the `gemini` CLI is
absent from PATH. Only relevant if an `AgentSpec` binds `runtime_type=gemini`;
tracked under ROADMAP item 32 (devcontainer runtime parity).

## Next runnable step

Resolve the block on `fc5fa4c3` via the API (`edit_task` the evidence-less task,
or `start_replan`) to drive the plan past the block and surface the next layered
defect (enrichment / execution / real-agent runtime logs).
