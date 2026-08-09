#!/usr/bin/env bash
# Recreate the free-tier OpenRouter agent roster against a running orchestrator.
#
# Why this exists: `orchestrate seed demo` seeds capabilities, ONE default agent,
# a provider and the config keys. The six-agent roster this project actually runs
# on was built by hand on top of that, so a rebuilt guest lost it. The guest is
# cattle; the roster therefore lives here rather than only in a database.
#
# Idempotent by omission, not by cleverness: the API returns 409 on a duplicate,
# which this script reports and steps over. Re-running is safe.
#
#   export OPENROUTER_API_KEY=...        # required on first run only
#   export ORCHESTRATOR_API_TOKEN=...    # only if the server has auth enabled
#   ./seed-agents.sh [BASE_URL]          # default http://127.0.0.1:8000
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
AUTH=()
[[ -n "${ORCHESTRATOR_API_TOKEN:-}" ]] && AUTH=(-H "Authorization: Bearer $ORCHESTRATOR_API_TOKEN")

api() { # METHOD PATH [JSON]
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -o /tmp/seed-out -w '%{http_code}' -X "$method" "$BASE$path"
              -H 'Content-Type: application/json' "${AUTH[@]}")
  [[ -n "$body" ]] && args+=(-d "$body")
  curl "${args[@]}"
}

report() { # LABEL CODE
  case "$2" in
    2*) echo "  ok    $1" ;;
    409) echo "  exists $1" ;;
    *)  echo "  FAIL  $1 (HTTP $2)"; sed 's/^/         /' /tmp/seed-out; echo ;;
  esac
}

if ! curl -sS "${AUTH[@]}" "$BASE/health" >/dev/null 2>&1; then
  echo "ERROR: no orchestrator at $BASE. Start it first:" >&2
  echo "  uv run python -m agent_orchestrator.infra.cli.main serve --port 8000" >&2
  exit 1
fi

echo "== capabilities =="
while IFS='|' read -r id name desc; do
  report "$id" "$(api POST /api/capabilities \
    "{\"id\":\"$id\",\"name\":\"$name\",\"description\":\"$desc\",\"tools\":[]}")"
done <<'EOF'
backend|Backend|server-side code
frontend|Frontend|UI code
testing|Testing|tests and QA
test_authoring|Test authoring|authors authoritative tests before implementation
implementation|Implementation|implements changes against frozen tests
go|Go|Go language code
http|HTTP|HTTP server/handlers
json|JSON|JSON encoding
EOF

echo "== provider =="
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "  skip  openrouter (OPENROUTER_API_KEY unset — fine if it already exists)"
else
  report "openrouter" "$(api POST /api/providers "$(printf '{"name":"openrouter","base_url":"https://openrouter.ai/api/v1","api_key":"%s","capacity_scope":"per_model"}' "$OPENROUTER_API_KEY")")"
fi

echo "== models (free tier, max_inflight 2) =="
for m in \
  "nvidia/nemotron-3-ultra-550b-a55b:free" \
  "poolside/laguna-s-2.1:free" \
  "google/gemma-4-31b-it:free" \
  "openai/gpt-oss-20b:free" ; do
  report "$m" "$(api POST /api/providers/openrouter/models \
    "{\"name\":\"$m\",\"max_inflight\":2}")"
done

# Two retry shapes, reproduced as they were. The TDD pair carries
# kind_attempt_ceiling.verification_error=2 (un-freeze #17: a rejected candidate
# is retryable but capped); the plain implementers do not.
RETRY_CEIL='{"max_attempts":5,"initial_backoff_seconds":30.0,"backoff_multiplier":2.0,"max_backoff_seconds":900.0,"jitter_ratio":0.2,"kind_max_attempts":{"rate_limit":6,"connection_error":5},"kind_backoff_scale":{"rate_limit":4.0},"kind_attempt_ceiling":{"verification_error":2},"non_retryable_kinds":["auth_error","verification_error","token_limit"]}'
RETRY_PLAIN='{"max_attempts":5,"initial_backoff_seconds":30.0,"backoff_multiplier":2.0,"max_backoff_seconds":900.0,"jitter_ratio":0.2,"kind_max_attempts":{"rate_limit":6,"connection_error":5},"kind_backoff_scale":{"rate_limit":4.0},"non_retryable_kinds":["auth_error","verification_error","token_limit"]}'

IMPL_INSTR='Implement the task exactly as described.'
TEST_INSTR='You are a TEST AUTHOR working test-first (TDD). Do NOT implement the feature. Author executable, runnable tests that precisely specify the task'"'"'s acceptance criteria and will FAIL against the current code. Create real test files in the repo and provide the exact executable command(s) that run them. Your output MUST include at least one executable check; producing no executable checks is a failure.'

IMPL_CAPS='["backend","frontend","go","http","implementation","json","testing"]'
TEST_CAPS='["backend","frontend","go","http","implementation","json","test_authoring","testing"]'

agent() { # NAME ROLE MODEL_ROLE INSTRUCTIONS CAPS RETRY MODEL
  report "$1" "$(api POST /api/agents "$(python3 - "$@" <<'PY'
import json, sys
name, role, model_role, instr, caps, retry, model = sys.argv[1:8]
print(json.dumps({
    "name": name, "role": role, "model_role": model_role,
    "instructions": instr, "capability_ids": json.loads(caps),
    "default_retry": json.loads(retry), "runtime_type": "pi",
    "provider_id": "openrouter", "model_id": model,
}))
PY
)")"
}

echo "== agents =="
agent dev-agent          implementer smart "$IMPL_INSTR" "$IMPL_CAPS" "$RETRY_CEIL"  "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
agent test-agent         test_author smart "$TEST_INSTR" "$TEST_CAPS" "$RETRY_CEIL"  "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
agent impl-laguna-s-2.1  implementer smart "$IMPL_INSTR" "$IMPL_CAPS" "$RETRY_PLAIN" "openrouter:poolside/laguna-s-2.1:free"
agent impl-gemma-4-31b-it implementer cheap "$IMPL_INSTR" "$IMPL_CAPS" "$RETRY_PLAIN" "openrouter:google/gemma-4-31b-it:free"
agent impl-gpt-oss-20b   implementer cheap "$IMPL_INSTR" "$IMPL_CAPS" "$RETRY_PLAIN" "openrouter:openai/gpt-oss-20b:free"
agent test-laguna        test_author smart "$TEST_INSTR" "$TEST_CAPS" "$RETRY_PLAIN" "openrouter:poolside/laguna-s-2.1:free"

echo "== default agent =="
report "dev-agent is default" "$(api POST /api/agents/dev-agent/default)"

echo
echo "Config keys are NOT set here — they belong to the orchestrator scope and"
echo "'seed demo' owns them. Verify with 'config list' that these hold:"
echo "  reasoner.mode        = llm"
echo "  reasoner.provider_id = openrouter"
echo "  reasoner.model_id    = openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
echo "  agent_runner.mode    = real"
