#!/usr/bin/env bash
# Check everything a happy-path-v2 run needs BEFORE it starts spending.
#
#   ./scripts/preflight.sh 0 "$PROJECT_ID"    # expect stub + dry-run
#   ./scripts/preflight.sh 1 "$PROJECT_ID"    # expect llm + real
#
# A Tier 1 run costs money and takes minutes; discovering afterwards that the
# agent binary was missing, the key never resolved, or the project pointed at an
# auto-seeded empty repo is the expensive way to learn it. Every check here is a
# read the operator could do by hand — the point is that none of them get
# skipped.
#
# It also enforces the rule that makes runs comparable at all: **never mix
# modes.** Tier 0 is stub + dry-run, Tier 1 is llm + real. A stub reasoner
# driving a real agent is not a cheaper Tier 1; it is an uninterpretable run.
#
# Exit 0 when the requested tier is ready, 1 when a check failed, 2 when
# preflight itself could not run.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SEED_TAG="happy-path-v2-seed"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
pass() { printf '  [PASS] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAILED=$((FAILED + 1)); }
FAILED=0

TIER="${1:-}"; shift || true
PROJECT_ID=""
EXPECT_REASONER_MODEL=""
EXPECT_AGENT_MODEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --expect-reasoner-model) [[ $# -ge 2 ]] || die "$1 requires a value"; EXPECT_REASONER_MODEL="$2"; shift 2 ;;
    --expect-agent-model)    [[ $# -ge 2 ]] || die "$1 requires a value"; EXPECT_AGENT_MODEL="$2"; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$PROJECT_ID" ]] || die "unexpected argument: $1"; PROJECT_ID="$1"; shift ;;
  esac
done
[[ "$TIER" == "0" || "$TIER" == "1" ]] \
  || die "usage: preflight.sh <0|1> [project-id] [--expect-reasoner-model M] [--expect-agent-model M]"

command -v jq >/dev/null 2>&1 || die "jq is required"
api() { "$SCRIPT_DIR/api.sh" "$@"; }

# Mirrors praxis_orchestrator/infra/env_compat.py: an explicit home wins, a
# fresh install is ~/.praxis, and a pre-rename ~/.orchestrator is adopted in
# place rather than left behind holding the operator's only database.
ORCH_HOME="${PRAXIS_HOME:-${ORCHESTRATOR_HOME:-$HOME/.praxis}}"
[ -d "$ORCH_HOME" ] || [ ! -d "$HOME/.orchestrator" ] || ORCH_HOME="$HOME/.orchestrator"
REPO="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v2/repo}"

if [[ "$TIER" == "1" ]]; then
  WANT_REASONER=llm; WANT_RUNNER=real
else
  WANT_REASONER=stub; WANT_RUNNER=dry-run
fi

printf 'happy-path-v2 preflight — Tier %s (expecting reasoner=%s, runner=%s)\n\n' \
  "$TIER" "$WANT_REASONER" "$WANT_RUNNER"

# --- 1. control plane -------------------------------------------------------
REASONER="$(api GET /api/reasoner/status 2>/dev/null)" \
  || die "cannot reach the API at ${HAPPY_PATH_API:-http://127.0.0.1:8000} — is it started?"
RUNNER="$(api GET /api/runner/status)"

R_MODE="$(jq -r '.mode' <<<"$REASONER")"
[[ "$R_MODE" == "$WANT_REASONER" ]] \
  && pass "reasoner.mode = $R_MODE" \
  || fail "reasoner.mode = $R_MODE, expected $WANT_REASONER (never mix modes)"

R_MODEL="$(jq -r '.model_name // ""' <<<"$REASONER")"
jq -e '.valid' <<<"$REASONER" >/dev/null \
  && pass "reasoner config resolves: $(jq -r '.provider_name // "stub"' <<<"$REASONER")/${R_MODEL:--}" \
  || fail "reasoner config invalid: $(jq -r '.detail // "no detail"' <<<"$REASONER")"

# Assert the PIN, not merely that something resolved. A Phase 1 run is only
# evidence if you know which model produced it, and "some valid model" is how a
# run against the wrong state directory looks from here: every mode and binding
# check passes, and you pay to measure a model you never chose.
if [[ -n "$EXPECT_REASONER_MODEL" ]]; then
  [[ "$R_MODEL" == "$EXPECT_REASONER_MODEL" ]] \
    && pass "reasoner model is the pinned $EXPECT_REASONER_MODEL" \
    || fail "reasoner model is '$R_MODEL', pinned '$EXPECT_REASONER_MODEL' — wrong state directory or unseeded config?"
fi

A_MODE="$(jq -r '.mode' <<<"$RUNNER")"
[[ "$A_MODE" == "$WANT_RUNNER" ]] \
  && pass "agent_runner.mode = $A_MODE" \
  || fail "agent_runner.mode = $A_MODE, expected $WANT_RUNNER (never mix modes)"

jq -e '.valid' <<<"$RUNNER" >/dev/null \
  && pass "runner mode config valid" \
  || fail "runner config invalid: $(jq -r '.detail // "no detail"' <<<"$RUNNER")"

# --- 2. agent bindings and binaries ----------------------------------------
# Only meaningful in real mode: dry-run resolves no provider and needs no binary.
if [[ "$TIER" == "1" ]]; then
  BAD="$(jq -r '[.agents[] | select(.valid | not) | "\(.agent_name): \(.detail // "invalid")"] | join("; ")' <<<"$RUNNER")"
  [[ -z "$BAD" ]] \
    && pass "all agent bindings resolve: $(jq -r '[.agents[] | "\(.agent_name)->\(.runtime_type)/\(.model_name // "-")"] | join(", ")' <<<"$RUNNER")" \
    || fail "agent binding(s) broken: $BAD"

  if [[ -n "$EXPECT_AGENT_MODEL" ]]; then
    OFF="$(jq -r --arg m "$EXPECT_AGENT_MODEL" \
      '[.agents[] | select(.valid) | select((.model_name // "") != $m) | "\(.agent_name)=\(.model_name // "-")"] | join(", ")' <<<"$RUNNER")"
    [[ -z "$OFF" ]] \
      && pass "every bound agent uses the pinned $EXPECT_AGENT_MODEL" \
      || fail "agent(s) not on the pinned $EXPECT_AGENT_MODEL: $OFF"
  fi

  # A missing binary for a runtime nothing is bound to is not this run's problem.
  USED="$(jq -r '[.agents[] | select(.valid) | .runtime_type] | unique | join(" ")' <<<"$RUNNER")"
  for rt in $USED; do
    [[ "$rt" == "dry-run" ]] && continue
    if jq -e --arg n "$rt" '.binaries[] | select(.name == $n) | .ok' <<<"$RUNNER" >/dev/null 2>&1; then
      pass "runtime binary present: $rt"
    else
      fail "runtime binary MISSING for bound runtime $rt: $(jq -r --arg n "$rt" '.binaries[] | select(.name==$n) | .message // "not probed"' <<<"$RUNNER")"
    fi
  done
  jq -e '.binaries[] | select(.name == "git") | .ok' <<<"$RUNNER" >/dev/null \
    && pass "git binary present" || fail "git binary missing (the workspace needs it)"

  # NOT checked here: PRAXIS_MASTER_KEY. Preflight runs in the OPERATOR's
  # shell, but the process that decrypts the provider key is the WORKER — which
  # normally loads it from an env file preflight never sees. Testing the wrong
  # process's environment produced a confident false failure, which is worse than
  # no check: the binding validity reported above is what actually resolves the
  # provider row, and a genuinely missing key surfaces as AUTH_ERROR on the run.
else
  pass "agent bindings/binaries not required in dry-run"
fi

# --- 3. the repository the run will actually write --------------------------
# The trap this fixture found on its first live run: a project with no repo_url
# gets a fresh empty repo auto-seeded under PRAXIS_HOME, so the run
# "passes" against a tree the checker never looks at.
if [[ -n "$PROJECT_ID" ]]; then
  PROJECT="$(api GET /api/projects | jq -c --arg id "$PROJECT_ID" '.[] | select(.id == $id)')"
  if [[ -z "$PROJECT" ]]; then
    fail "no project $PROJECT_ID"
  else
    BOUND="$(jq -r '.repo_url // ""' <<<"$PROJECT")"
    if [[ -z "$BOUND" ]]; then
      fail "project has NO repo_url — the run will branch an auto-seeded empty repo, not $REPO"
    elif [[ "$BOUND" != "$REPO" ]]; then
      fail "project repo_url is $BOUND but this preflight is checking $REPO"
    else
      pass "project bound to $BOUND"
    fi
  fi
else
  printf '  [SKIP] project binding (pass the project id to check it)\n'
fi

if [[ ! -d "$REPO/.git" ]]; then
  fail "no git repository at $REPO — run materialize.sh"
else
  pass "git repository present at $REPO"
  git -C "$REPO" rev-parse "$SEED_TAG" >/dev/null 2>&1 \
    && pass "$SEED_TAG present" || fail "$SEED_TAG missing — run materialize.sh"
  [[ -z "$(git -C "$REPO" status --porcelain)" ]] \
    && pass "working tree clean" || fail "working tree dirty — run reset.sh before a measured run"
  STALE="$(git -C "$REPO" for-each-ref --format='%(refname:short)' \
    refs/heads/plan refs/heads/cycle refs/heads/goal refs/heads/task | wc -l)"
  [[ "$STALE" -eq 0 ]] \
    && pass "no branches from previous runs" \
    || fail "$STALE branch(es) from previous runs — run reset.sh so this run is comparable"
fi

# --- 4. the verification command the contract will freeze -------------------
# `python -m pytest -q` is what brief.txt promises and what the task contract
# will name. If it cannot EXECUTE, every attempt fails on infrastructure
# (126/127 -> retryable TOOL_ERROR) and burns the retry budget on a setup
# problem.
#
# Collecting nothing is NOT a problem here: v2 ships `tests/` empty on purpose —
# the agent authors the check. pytest exit 5 means "no tests collected", which is
# the correct state before a run. Only a usage/internal error (2, 3, 4) or a
# missing pytest is a real finding.
if [[ -d "$REPO" ]]; then
  set +e
  (cd "$REPO" && PYTHONPATH="$REPO/src" python3 -m pytest --collect-only -q >/dev/null 2>&1)
  collect_status=$?
  set -e
  case "$collect_status" in
    0) pass "verification command runs; tests already present" ;;
    5) pass "verification command runs; tests/ empty as expected (the agent authors it)" ;;
    127) fail "pytest not importable — the command will exit 127 in every attempt" ;;
    *) fail "pytest exits $collect_status in $REPO (usage or internal error, not an empty suite)" ;;
  esac
fi

printf '\n'
if [[ "$FAILED" -eq 0 ]]; then
  printf 'preflight clean — Tier %s is ready to run\n' "$TIER"
  exit 0
fi
printf '%d preflight check(s) failed — fix these before spending a run\n' "$FAILED"
exit 1
