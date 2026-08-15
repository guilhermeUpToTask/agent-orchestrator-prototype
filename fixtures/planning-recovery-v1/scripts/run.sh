#!/usr/bin/env bash
# Planning recovery v1 — starve enrichment, then prove the retry is better off.
#
# Drives the failure through `reasoner.max_turns=1` rather than hoping a model
# writes a bad contract: every enrichment session then dies on its turn budget
# before submitting, on any model, every run.
#
#   HAPPY_PATH_API   API base URL (default http://127.0.0.1:8000)
#   PROJECT_ID       existing project to reuse (default: create one)
#   HAPPY_PATH_REPO  repo the project points at (default the happy-path-v1 seed)
set -Eeuo pipefail
IFS=$'\n\t'

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$HERE/../../.." && pwd -P)"
API_SH="$ROOT/fixtures/happy-path-v1/scripts/api.sh"

export HAPPY_PATH_API="${HAPPY_PATH_API:-http://127.0.0.1:8000}"
# Mirrors praxis_orchestrator/infra/env_compat.py: an explicit home wins, a
# fresh install is ~/.praxis, and a pre-rename ~/.orchestrator is adopted in
# place rather than left behind holding the operator's only database.
ORCH_HOME="${PRAXIS_HOME:-${ORCHESTRATOR_HOME:-$HOME/.praxis}}"
[ -d "$ORCH_HOME" ] || [ ! -d "$HOME/.orchestrator" ] || ORCH_HOME="$HOME/.orchestrator"
REPO="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"

die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }

api() { "$API_SH" "$@"; }

command -v jq >/dev/null 2>&1 || die "jq is required"
[[ -x "$API_SH" ]] || die "missing $API_SH"

setup_error() { printf 'SETUP: %s\n' "$*" >&2; exit 2; }

mode="$(api GET /api/reasoner/status | jq -r .mode)"
[[ "$mode" == "llm" ]] || setup_error "needs a real reasoner (reasoner.mode=llm); got '$mode'"

# The starvation MUST already be in effect before the worker booted.
# `AppContainer.reasoner` is a cached_property resolved once at worker startup,
# so writing reasoner.max_turns now would be accepted, reported back by
# GET /api/reasoner/status, and silently ignored by the running worker — which is
# exactly how this fixture found that staleness in the first place.
TURNS="$(api GET /api/config/orchestrator | jq -r '."reasoner.max_turns" // "8"')"
if [[ "$TURNS" != "1" ]]; then
  setup_error "$(cat <<'EOS'
reasoner.max_turns is not 1, so enrichment will not be starved.

Set it and RESTART the worker (config is read once at worker boot):

  ./fixtures/happy-path-v1/scripts/api.sh PUT \
      /api/config/orchestrator/reasoner.max_turns '{"value":"1"}'
  # restart the worker, then re-run this script

Restore it the same way (value "8") plus a restart when you are done.
EOS
)"
fi
note "starvation precondition satisfied: reasoner.max_turns=1"

# ---------------------------------------------------------------- setup
PROJECT_ID="${PROJECT_ID:-$(api POST /api/projects \
  "{\"name\":\"planning-recovery-v1\",\"repo_url\":\"$REPO\"}" | jq -r .id)}"
BRIEF="$(jq -Rs . < "$ROOT/fixtures/happy-path-v1/brief.txt")"
PLAN_ID="$(api POST /api/plans "{\"brief\":$BRIEF,\"project_id\":\"$PROJECT_ID\"}" | jq -r .plan_id)"
note "plan=$PLAN_ID project=$PROJECT_ID"

gate_body() {
  api GET "/api/plans/$PLAN_ID" \
    | jq -c '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision}'
}

wait_for_gate() {
  local want="$1" tries="${2:-25}"
  for _ in $(seq 1 "$tries"); do
    [[ "$(api GET "/api/plans/$PLAN_ID" | jq -r '.pending_gate.subject_type // "none"')" == "$want" ]] \
      && return 0
    sleep 6
  done
  die "gate '$want' never opened"
}

for _ in 1 2 3; do
  [[ "$(api GET "/api/plans/$PLAN_ID" | jq -r '.pending_gate.subject_type // "none"')" == "intent" ]] && break
  api POST "/api/plans/$PLAN_ID/discovery/message" \
    '{"message":"Yes, that is right. Proceed."}' >/dev/null
done
wait_for_gate intent
api POST "/api/plans/$PLAN_ID/intent/approve" "$(gate_body)" >/dev/null
note "intent approved — the starved reasoner now hits cycle architecture"

# `reasoner.max_turns` bounds EVERY reasoner session, so the first planning stage
# after the intent gate is the one that starves: cycle architecture, before a
# draft or a goal exists. That is the subject here — the stage is incidental, the
# question is whether a dead planning session leaves anything behind.
PURPOSE=cycle_architecture
artifacts() { api GET "/api/plans/$PLAN_ID/planning-artifacts?purpose=$PURPOSE"; }

for _ in $(seq 1 40); do
  [[ "$(artifacts | jq 'length')" -ge 2 ]] && break
  sleep 6
done

BODY="$(artifacts)"
COUNT="$(echo "$BODY" | jq 'length')"

if [[ "$(api GET "/api/plans/$PLAN_ID" | jq -r '.pending_gate.subject_type // "none"')" == "cycle_draft" ]]; then
  setup_error "architecture SUCCEEDED: the worker is not running with max_turns=1 (restart it)"
fi

# 1. evidence exists at all
[[ "$COUNT" -ge 1 ]] || die "a starved attempt left no evidence; the retry would restart from nothing"
ok "attempts recorded: $COUNT"

# 2. they accumulate rather than overwrite
if [[ "$COUNT" -ge 2 ]]; then
  SEQS="$(echo "$BODY" | jq -c '[.[].sequence]')"
  [[ "$(echo "$BODY" | jq '[.[].sequence] | unique | length')" == "$COUNT" ]] \
    || die "sequences collide ($SEQS): attempts are overwriting each other"
  ok "sequences distinct: $SEQS"
else
  note "only one attempt recorded; skipping the accumulation check"
fi

# 3. an abandoned attempt never becomes advice
echo "$BODY" | jq -e 'all(.[]; .outcome != "abandoned" or (.rejection_reasons | length) == 0)' \
  >/dev/null || die "an abandoned attempt carries rejection advice it never earned"
ok "abandoned attempts carry no invented advice"

# 4. the turn cost is recorded — the evidence the escalating budget reads
echo "$BODY" | jq -e 'all(.[]; .turns_used != null)' >/dev/null \
  || die "attempts record no turn cost, so nothing can grant the retry more room"
ok "turn cost recorded on every attempt"

echo "$BODY" | jq -r '.[] | "  attempt \(.sequence): \(.outcome), turns=\(.turns_used)"'

# 5. the operator escape hatch really clears it
api DELETE "/api/plans/$PLAN_ID/planning-artifacts?purpose=$PURPOSE" >/dev/null
[[ "$(artifacts | jq 'length')" == "0" ]] || die "reset did not clear the planning memory"
ok "operator reset clears the memory"

printf '\nPASS — a starved planning attempt leaves evidence the retry can use.\n'
printf '\nRecovery half (manual, because the budget is read at worker boot):\n'
printf '  1. restore reasoner.max_turns to 8 and restart the worker\n'
printf '  2. resolve the block:  POST /api/plans/%s/retry-stage\n' "$PLAN_ID"
printf '  3. the stage should now pass with no further operator action\n'
