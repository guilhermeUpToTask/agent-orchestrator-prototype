#!/usr/bin/env bash
# Parallel goals v1 — two goals, one cycle branch, a moved merge base. Tier 0.
#
#   HAPPY_PATH_API   API base URL (default http://127.0.0.1:8000)
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
setup_error() { printf 'SETUP: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }
ok() { printf '  ok  %s\n' "$*"; }
api() { "$API_SH" "$@"; }

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v git >/dev/null 2>&1 || die "git is required"
[[ -d "$REPO/.git" ]] || setup_error "no repo at $REPO (run happy-path-v1/scripts/materialize.sh)"

[[ "$(api GET /api/reasoner/status | jq -r .mode)" == "stub" ]] \
  || setup_error "needs reasoner.mode=stub (Tier 0)"
[[ "$(api GET /api/runner/status | jq -r .mode)" == "dry-run" ]] \
  || setup_error "needs agent_runner.mode=dry-run (Tier 0)"

PROJECT_ID="$(api POST /api/projects \
  "{\"name\":\"parallel-goals-v1\",\"repo_url\":\"$REPO\"}" | jq -r .id)"
BRIEF="$(jq -Rs . < "$ROOT/fixtures/happy-path-v1/brief.txt")"
PLAN_ID="$(api POST /api/plans "{\"brief\":$BRIEF,\"project_id\":\"$PROJECT_ID\"}" | jq -r .plan_id)"
note "plan=$PLAN_ID"

plan() { api GET "/api/plans/$PLAN_ID"; }
gate_body() {
  plan | jq -c '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision}'
}
wait_for() {
  for _ in $(seq 1 40); do
    plan | jq -e "$1" >/dev/null 2>&1 && return 0
    sleep 3
  done
  die "timed out waiting for $2 (state: $(plan | jq -c '{status, activity}'))"
}

for _ in 1 2 3; do
  [[ "$(plan | jq -r '.pending_gate.subject_type // "none"')" == "intent" ]] && break
  api POST "/api/plans/$PLAN_ID/discovery/message" '{"message":"Proceed."}' >/dev/null
done
api POST "/api/plans/$PLAN_ID/intent/approve" "$(gate_body)" >/dev/null
wait_for '.pending_gate.subject_type == "cycle_draft"' "the cycle draft gate"

# The stub only ever drafts one goal. A draft sits at a REVIEW GATE, where
# nothing is executing, so it can simply be revised into two independent goals —
# no race, no model, no cost. `depends_on: []` on both is what makes them
# parallel and what makes the second merge hit a base the first has moved.
note "revising the draft into two independent goals"
api PUT "/api/plans/$PLAN_ID/cycle-draft" "$(jq -nc '{
  goals: [
    {key:"alpha", name:"Alpha", objective:"first independent goal",
     position:0, depends_on:[]},
    {key:"beta",  name:"Beta",  objective:"second independent goal",
     position:1, depends_on:[]}
  ]}')" >/dev/null

DRAFT_GOALS="$(plan | jq '.cycle_draft.goals | length')"
[[ "$DRAFT_GOALS" == "2" ]] || die "the draft did not take two goals (got $DRAFT_GOALS)"
api POST "/api/plans/$PLAN_ID/cycle-draft/approve" "$(gate_body)" >/dev/null
ok "two-goal cycle activated"

wait_for '.pending_gate.subject_type == "cycle_completion" or .status == "idle"' "publication"

# 1. both goals actually ran
STATUSES="$(plan | jq -c '[.goals[] | .status] | sort')"
[[ "$STATUSES" == '["done","done"]' ]] || die "goals did not both complete: $STATUSES"
ok "both goals DONE: $STATUSES"

# 2. no block of any kind
BLOCKS="$(plan | jq '(.goal_blocks | length) + (if .block == null then 0 else 1 end)')"
[[ "$BLOCKS" == "0" ]] || die "a block was raised: $(plan | jq -c '.goal_blocks')"
ok "no blocks raised"

# 3. both goal branches merged into the cycle branch — the point of the fixture:
#    the second merge ran against a base the first had already moved.
CYCLE_ID="$(plan | jq -r '.active_cycle.id')"
MERGES="$(git -C "$REPO" log --oneline "cycle/$CYCLE_ID" | grep -c '^[0-9a-f]* merge: goal/' || true)"
[[ "$MERGES" == "2" ]] || die "expected 2 goal merges on cycle/$CYCLE_ID, found $MERGES"
ok "both goal branches merged into cycle/$CYCLE_ID"

git -C "$REPO" log --oneline "cycle/$CYCLE_ID" | head -4 | sed 's/^/  /'

printf '\nPASS — two goals promoted into one cycle branch; the moved merge base held.\n'
printf '\nNote: dry-run gives each task its own artifact, so two goals never touch the\n'
printf 'same file and this cannot produce a merge CONFLICT. A real conflict needs\n'
printf 'Tier 1 with overlapping scope — see the README.\n'
