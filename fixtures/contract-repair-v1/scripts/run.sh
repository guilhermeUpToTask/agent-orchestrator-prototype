#!/usr/bin/env bash
# Contract repair v1 — poison a frozen contract with a command that can never
# pass, then prove the orchestrator repairs it in place instead of blocking.
# Tier 1 only (see README: Tier 0 has no operator window at the contract
# boundary, and the stub's contract is satisfiable by construction).
set -Eeuo pipefail
IFS=$'\n\t'

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$HERE/../../.." && pwd -P)"
API_SH="$ROOT/fixtures/happy-path-v1/scripts/api.sh"

export HAPPY_PATH_API="${HAPPY_PATH_API:-http://127.0.0.1:8000}"
ORCH_HOME="${PRAXIS_HOME:-$HOME/.praxis}"
REPO="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"
WORKER_LOG="${CONTRACT_REPAIR_WORKER_LOG:-}"

REAL_TEST="tests/test_greeter.py"   # what the seed repository actually has
FAKE_TEST="tests/test_greet.py"     # the near-twin that can never pass

die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
setup_error() { printf 'SETUP: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
api() { "$API_SH" "$@"; }

command -v jq >/dev/null 2>&1 || setup_error "jq is required"
[[ -d "$REPO/.git" ]] || setup_error "no repo at $REPO (run happy-path-v1/scripts/materialize.sh)"
[[ -f "$REPO/$REAL_TEST" ]] || setup_error "$REPO/$REAL_TEST missing — is this the happy-path seed?"
[[ "$(api GET /api/reasoner/status | jq -r .mode)" == "llm" ]] \
  || setup_error "needs reasoner.mode=llm (Tier 1)"
[[ "$(api GET /api/runner/status | jq -r .mode)" == "real" ]] \
  || setup_error "needs agent_runner.mode=real (Tier 1)"

DEADLOCKS_BEFORE=0
if [[ -n "$WORKER_LOG" && -f "$WORKER_LOG" ]]; then
  DEADLOCKS_BEFORE="$(grep -c 'Database stayed locked' "$WORKER_LOG" || true)"
fi

note "opening a plan on the happy-path seed"
"$ROOT/fixtures/happy-path-v1/scripts/reset.sh" >/dev/null 2>&1 || true
PROJECT_ID="$(api POST /api/projects \
  "{\"name\":\"contract-repair-v1-$RANDOM\",\"repo_url\":\"$REPO\"}" | jq -r .id)"
BRIEF="$(jq -Rs . < "$ROOT/fixtures/happy-path-v1/brief.txt")"
RESP="$(api POST /api/plans "{\"brief\":$BRIEF,\"project_id\":\"$PROJECT_ID\"}")"
PLAN_ID="$(echo "$RESP" | jq -r .plan_id)"
note "plan=$PLAN_ID"

plan_json() { api GET "/api/plans/$PLAN_ID"; }
gate_of() { plan_json | jq -r '.pending_gate.subject_type // "none"'; }
gate_body() { plan_json | jq -c '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision}'; }
head_task() { plan_json | jq -c '.active_cycle.goals[0].tasks[0] // null'; }

if [[ "$(echo "$RESP" | jq -r .discovery_status)" != "committed" ]]; then
  for _ in 1 2 3; do
    [[ "$(gate_of)" == "intent" ]] && break
    api POST "/api/plans/$PLAN_ID/discovery/message" \
      '{"message":"Yes. Success: python -m pytest -q green, default branch untouched. Proceed."}' >/dev/null || true
    sleep 3
  done
fi
for _ in $(seq 1 60); do [[ "$(gate_of)" == "intent" ]] && break; sleep 5; done
[[ "$(gate_of)" == "intent" ]] || die "never reached the intent gate"
api POST "/api/plans/$PLAN_ID/intent/approve" "$(gate_body)" >/dev/null
ok "intent approved"

for _ in $(seq 1 90); do [[ "$(gate_of)" == "cycle_draft" ]] && break; sleep 5; done
[[ "$(gate_of)" == "cycle_draft" ]] || die "never reached the cycle-draft gate"
api POST "/api/plans/$PLAN_ID/cycle-draft/approve" "$(gate_body)" >/dev/null
GOAL_ID="$(plan_json | jq -r '.active_cycle.goals[0].id')"
ok "cycle activated (goal $GOAL_ID)"

# RACING A NARROW WINDOW, deliberately and visibly. `update_task_contract`
# needs the task PENDING (or FAILED while paused) — never DONE — so the pause
# has to settle on a TDD stage boundary, between the test-author attempt and the
# implementation attempt. Poll hard and fire the pause the instant a contract
# exists: enrichment and the first attempt run under ONE claim, so there is no
# supported way to hold the plan at the contract boundary. That is the deferred
# "no operator control point at the contract boundary" item, and this loop is
# what living without it costs.
note "racing for the contract-boundary window (fast poll)"
for _ in $(seq 1 600); do
  [[ "$(head_task | jq -r '.contract // "null"')" != "null" ]] && break
  sleep 0.3
done
TASK_ID="$(head_task | jq -r '.id')"
[[ "$(head_task | jq -r '.contract // "null"')" != "null" ]] \
  || die "contract never froze — cannot poison what does not exist"
api POST "/api/plans/$PLAN_ID/pause" '{"reason":"contract-repair-v1"}' >/dev/null 2>&1 || true
ok "contract frozen for task $TASK_ID; pause requested"

for _ in $(seq 1 90); do [[ "$(plan_json | jq -r .status)" == "paused" ]] && break; sleep 2; done
[[ "$(plan_json | jq -r .status)" == "paused" ]] || die "plan never settled PAUSED"
TASK_STATUS="$(head_task | jq -r '.status')"
if [[ "$TASK_STATUS" != "pending" && "$TASK_STATUS" != "failed" ]]; then
  printf 'SETUP: lost the race — the task was %s when the pause settled, and only\n' "$TASK_STATUS" >&2
  printf '       PENDING (or FAILED while paused) is editable. The goal finished\n' >&2
  printf '       before the pause landed on a stage boundary. Re-run; if it keeps\n' >&2
  printf '       losing, that IS the finding — see the deferred contract-boundary\n' >&2
  printf '       control point in ROADMAP.md. Never reported as a FAIL, because\n' >&2
  printf '       nothing about the orchestrator misbehaved.\n' >&2
  exit 2
fi
ok "paused with the task $TASK_STATUS — window taken"

note "poisoning the verification command: $FAKE_TEST (real file is $REAL_TEST)"
api POST "/api/plans/$PLAN_ID/edits" "$(jq -nc \
  --arg g "$GOAL_ID" --arg t "$TASK_ID" --arg cmd "python -m pytest -q $FAKE_TEST" \
  '{type:"update_task_contract", goal_id:$g, task_id:$t, verification_commands:[$cmd]}')" >/dev/null
POISONED="$(head_task | jq -r '.contract.verification_commands[0]')"
[[ "$POISONED" == *"$FAKE_TEST"* ]] || die "poison did not stick: $POISONED"
ok "contract now runs a command that cannot pass"

api POST "/api/plans/$PLAN_ID/resume" '{}' >/dev/null
ok "resumed"

note "watching for the repair (or a block, which is the failure this guards)"
REPAIR_SEEN=0
for i in $(seq 1 200); do
  CMD="$(head_task | jq -r '.contract.verification_commands[0] // ""')"
  STATUS="$(head_task | jq -r '.status')"
  BLOCK="$(plan_json | jq -c '(.block // (.goal_blocks | if .=={} then null else . end))')"
  [[ "$BLOCK" != "null" ]] && { printf 'FAIL: goal blocked instead of repairing:\n%s\n' "$BLOCK" >&2; exit 1; }
  if [[ "$CMD" == *"$REAL_TEST"* && "$REPAIR_SEEN" -eq 0 ]]; then
    REPAIR_SEEN=1
    ok "contract repaired -> $CMD"
  fi
  # Keep going until the task actually finishes: the repair only requeues it,
  # and the attempt that proves the repair worked runs AFTER it.
  [[ "$STATUS" == "done" ]] && break
  (( i % 10 == 0 )) && note "  [$i] task=$STATUS cmd=$CMD"
  sleep 5
done

note "assertions"
# `purpose` defaults to goal_contract and must be asked for explicitly; the
# payload is deliberately not served, so assert on outcome, not on contents.
REPAIRS="$(api GET "/api/plans/$PLAN_ID/planning-artifacts?purpose=contract_repair&goal_id=$GOAL_ID" \
  2>/dev/null || echo '[]')"
[[ "$(echo "$REPAIRS" | jq 'length')" -ge 1 ]] \
  || die "2. no contract_repair artifact was recorded (the deadlock signature: repaired forever, persisted never)"
[[ "$(echo "$REPAIRS" | jq -r '.[0].outcome')" == "committed" ]] \
  || die "2. contract_repair recorded but not committed: $(echo "$REPAIRS" | jq -c '.[0]')"
ok "2. contract_repair recorded and committed"

ATTEMPTS="$(api GET "/api/plans/$PLAN_ID/attempts" | jq -c '[.tasks[]?.runs[]?.attempts[]? | {number, status, failure_kind}]')"
echo "$ATTEMPTS" | jq -e 'any(.[]; .status == "failed")' >/dev/null \
  || die "1. no attempt failed — the poison never took effect: $ATTEMPTS"
ok "1. contract-shaped failure observed: $ATTEMPTS"

FINAL_CMD="$(head_task | jq -r '.contract.verification_commands[0] // ""')"
[[ "$FINAL_CMD" == *"$REAL_TEST"* ]] || die "3. contract still names a path that does not exist: $FINAL_CMD"
ok "3. contract names the real path"

FINAL_STATUS="$(head_task | jq -r '.status')"
[[ "$FINAL_STATUS" == "done" ]] || die "4. task did not complete after the repair (status=$FINAL_STATUS)"
[[ "$(plan_json | jq -c '(.block // (.goal_blocks | if .=={} then null else . end))')" == "null" ]] \
  || die "4. a block was opened — a human was asked"
ok "4. task DONE, no block, nobody asked"

if [[ -n "$WORKER_LOG" && -f "$WORKER_LOG" ]]; then
  AFTER="$(grep -c 'Database stayed locked' "$WORKER_LOG" || true)"
  (( AFTER == DEADLOCKS_BEFORE )) \
    || die "5. $((AFTER - DEADLOCKS_BEFORE)) new 'Database stayed locked' event(s) — the repair write is deadlocking again"
  ok "5. zero new deadlock events"
else
  printf '  --  5. deadlock check SKIPPED (set CONTRACT_REPAIR_WORKER_LOG)\n'
fi

printf '\nCONTRACT REPAIR v1 GREEN — plan %s\n' "$PLAN_ID"
