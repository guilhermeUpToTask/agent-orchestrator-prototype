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

## Finding #7 — seeded registry can't cover a real plan's capabilities (agent_capability block at enrichment)  ⚠ OPEN (gap, ROADMAP #24)

Driving the fresh `ae47ee19` cycle (activated with 2 goals: implement `/healthz`
+ its test) through **architecture → active cycle** was clean (OpenRouter
responding). Enrichment froze goal 0's `GoalContract` with a `test_author` task
requiring capabilities **`['go','http','json','test_authoring']`**, then opened
an `agent_capability` block: *"no configured agent covers test_author"*. The
`orchestrate seed demo` registry only defines `[backend, frontend,
implementation, test_authoring, testing]` — `go`/`http`/`json` don't exist, so
**any real plan whose reasoner-generated tasks name domain capabilities the seed
lacks blocks immediately at enrichment**. This is the coverage-preflight gap of
ROADMAP item 24 (registry-defined execution profiles + a role×capability matrix
warned about *before* enrichment).

**Resolved operationally (via the API, to continue the walkthrough):** created
`go`/`http`/`json` capabilities (`POST /api/capabilities`), added them to
`dev-agent` (`PUT /api/agents/dev-agent`, keeps its `pi` + openrouter/nemotron
binding), then `POST …/retry-stage` — un-freeze #7's live-registry recovery
re-resolved the roles and the cycle advanced into **execution** (`activity=task:…`,
real pi agent). Not a code defect; an operability gap worth an evidence-gated
preflight. Available runtimes confirmed via `/api/runner/status`: `git`, `pi`
0.73.1, `claude` 2.1.215 ok; `gemini` missing.

## Finding #8 — agent model binding invalid for its runtime (pi can't resolve the openrouter model)  ⚠ OPEN (config)

Execution under the real `pi` agent failed 3 policy attempts with
`TaskFailed: Error: Model "nvidia/nemotron-3-ultra-550b-a55b:free" not found. Use
--list-models to see available models.` `dev-agent` is bound to the same
openrouter/nemotron model as the reasoner, but the `pi` CLI backend doesn't
resolve that model id, so every attempt fails and opens a correct
`execution_failure` block (retry/block machinery works — 0 tick_failed, 3
attempts then block). There is no registry validation that an agent's
`model_id` is actually resolvable by its `runtime_type`; a bad binding is only
discovered at execution time as a terminal per-task failure. **Worked around**
by switching `dev-agent.runtime_type` to `dry-run` (deterministic artifacts
through the real Git/verify/promote/publish machinery) to drive the cycle to
completion; the real-runtime model-resolution binding remains a config gap.

## Finding #9 — process-observation persistence always fails (telemetry silently dropped)  🔧 FIX DISPATCHED

Every agent run logs (12×) `runtime.process_observation_persist_failed` with
`ValueError: process observations require the process repository extension`
(`observation_repository.py:151`, from `cli_runner._persist_observations`).
Non-fatal — `_persist_observations` catches and logs — but it means process
telemetry observations are **never persisted** (the cli_runner is wired with a
base observation repository lacking the process extension). The runtime-log /
observation feed is therefore incomplete. Dispatched to codex
(`--sandbox danger-full-access`, own branch) to wire the process-capable
observation repository (or make `append` accept process observations).

## Finding #10 — dry-run can't complete a task with a real verification command  ⚠ OPEN (env constraint)

The dry-run workaround for #8 applied (attempts 4-5 ran `runtime=dry-run`), but
each failed at verification: the frozen `TaskContract`'s command is a real
`go test -v ./internal/handler/...`, which fails against `DummyAgentRunner`'s
placeholder artifacts (no real Go project). So **neither path completes the
cycle**: the real pi agent is blocked by the #8 model-string bug, and dry-run
can't satisfy a real language-specific verification. Cycle completion here
therefore depends on the **#8 fix** (dispatched to codex) landing so a real
agent can produce code that passes the frozen `go test`. Until then the loop's
"cycle finished" stop-condition is not autonomously reachable, and the
OpenRouter *reasoner* is not the blocker (it drove discovery→architecture→
enrichment fine), so the "rate limit exhausted" stop-condition isn't triggered
either. Progress now runs through fixing #8/#9, not driving the plan.

## Walkthrough outcome & stop reason (loop terminated: human gate)

After the #8 code fix (strip the `<provider>:` prefix so pi gets the bare model
name) landed and was verified live (no more prefix issue, 0 obs-persist errors),
pi **still** reports `Model "nvidia/nemotron-3-ultra-550b-a55b:free" not found`.
`pi models` confirms the reason: that model **is not in pi's OpenRouter catalog
at all**, and pi's catalog has **zero `:free` models** — every usable model is
paid (claude/gpt/gemini/…). So:

- **Real agent path:** blocked — `dev-agent` is bound to a model pi doesn't have,
  and the only working alternatives are **paid** models (a spend/consent
  decision, against the "be wise with token consumption" guidance).
- **dry-run path:** blocked — can't satisfy the frozen real `go test` (#10).
- **Reasoner (OpenRouter):** works fine — so the "rate limit exhausted"
  stop-condition never triggers.

**Neither loop stop-condition is autonomously reachable.** Cycle completion now
needs a human decision: authorize binding `dev-agent` to an available (paid)
model — e.g. `anthropic/claude-3.5-haiku` — or accept the demo can't complete a
real Go cycle in this environment. The loop is therefore stopped here.

### What the walkthrough delivered (branch `walkthrough/api-2026-07-20`)
Code fixes (all verified, ruff+mypy+tests green): **#1** goal-promotion storm →
recoverable block (+regression test); **#4** honest block resolutions;
**#6** reasoner 500 on intent-with-questions → question turn (codex, no bwrap);
**#8** pi model-string prefix (codex, no bwrap); **#9** process-observation
persistence wiring (codex, no bwrap). Open/human-gate findings: **#2/#5** pause &
start_replan rejected by legacy-phase guards (needs domain un-freeze); **#3**
navigation vs promotion predicate mismatch; **#7** seed capability-coverage gap
(ROADMAP #24); **#8 (config half)** no free pi model / model-not-in-catalog;
**#10** dry-run vs real verification. codex-without-bubblewrap
(`--sandbox danger-full-access`) worked reliably for all three delegated fixes.

## RESOLUTION — #8 fully fixed; loop reached the OpenRouter rate-limit stop condition

The earlier "human gate" stop was premature. The real root of #8 was a **missing
`--provider`**: the pi runner passed `--model` but not `--provider`, so pi
couldn't resolve the OpenRouter model. Live-proven:
`pi --model nvidia/nemotron-…:free` → not found; `pi --provider openrouter
--model nvidia/nemotron-…:free` → **OK**. Fix: `_build_cmd` now passes
`--provider <backend>` alongside the bare `--model` (both the prefix-strip and
the provider are required). After it landed, the **real pi agent ran the model**
(attempt 10, 0 "Model not found").

The cycle then hit the loop's **defined terminal condition**: the free nemotron
model returned empty with `ResourceExhausted: Worker local total request limit
reached (57/32)` — **the OpenRouter model's rate limit is exhausted**. The
downstream "test author produced no executable checks" block is a symptom of the
rate-limited empty response, not a defect. The loop stopped here as specified
(cycle finished OR rate limit exhausted).

Total code fixes this walkthrough (all verified): #1 storm→block (+test),
#4 honest resolutions, #6 reasoner question-turn, #8a pi model-string prefix,
**#8b pi `--provider`**, #9 process-observation persistence. To continue past
the rate limit: wait for the free-tier reset, or bind a paid/stronger model
(a spend decision) — routing the `test_author` role to a stronger model is
ROADMAP #35.

## Finding #11 — free model won't respect the TDD test_author role (writes production code)  ⚠ OPEN (model quality, ROADMAP #35)

With the burst limit cleared, the real pi/nemotron agent produced actual Go code
(attempt 11: `go.mod`, `cmd/server/main.go`, `internal/handler/healthz.go`,
`healthz_test.go`) — but the task was **correctly rejected**: *"test author
modified production paths"*. The `test_author` role must write ONLY tests; the
scope guard caught it writing the full implementation. The prompt is already
explicit (`build_task_prompt`, cli_runner.py:133: "Write ONLY tests that fail for
the right reason; never modify production files", plus allowed/forbidden scope
paths). So this is **not** a fixable prompt/code defect — the free
`nemotron-3-ultra:free` model ignores the role constraint. This is the
model-quality / role-adherence limitation of ROADMAP #35 (route `test_author` to
a stronger model). Not a system defect; the TDD scope enforcement works
correctly. Continuing requires a stronger (paid) model for the role — a
spend/human-gate decision.

## RESOLUTION #2/#5 — cyclic-aware pause & replan guards (domain unfreeze #9)  ✅ FIXED & live-verified

User-authorized domain un-freeze (decision 49). `Plan.request_pause`, `Plan.pause`,
and `Plan.begin_replanning` now consult the cyclic authority (`active_cycle` /
`status`) instead of only the legacy `PlanPhase`, exactly as `resume()` already
did — so a running/blocked cyclic plan whose legacy phase is `REPLANNING` can
actually be paused/replanned, matching its advertised `legal_actions`.

**Live-verified on the previously-wedged `fc5fa4c3`:** `POST /replan` went from
`422 INVALID_TRANSITION` → **204**, moving the plan from `blocked` (unrecoverable)
to `paused` / `activity=cycle_architecture` with `legal_actions=[resume,
start_replan, edit_pending_work]` — the wedge is cleared, the plan is recoverable
through the API. 272 orchestration truth-tests pass; ruff + mypy clean. Regression
tests added separately.

## Environment note (not a plan defect)

Worker boot warns `worker.dependency_missing binary=gemini` — the `gemini` CLI is
absent from PATH. Only relevant if an `AgentSpec` binds `runtime_type=gemini`;
tracked under ROADMAP item 32 (devcontainer runtime parity).

## Next runnable step

Resolve the block on `fc5fa4c3` via the API (`edit_task` the evidence-less task,
or `start_replan`) to drive the plan past the block and surface the next layered
defect (enrichment / execution / real-agent runtime logs).
