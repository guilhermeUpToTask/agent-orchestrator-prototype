#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(cd -- "$BACKEND_DIR/.." && pwd -P)"
FRONTEND_DIR="$REPO_ROOT/frontend"

ENV_FILE="${DEV_ENV_FILE:-$BACKEND_DIR/.env}"
LOAD_ENV=true

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

usage() {
  cat <<'EOF'
Usage: backend/scripts/dev.sh [global options] <command> [command options]

Hardened local-development entry point for Agent Orchestrator.

Commands:
  doctor   Validate required tools and repository inputs (read-only)
  setup    Install locked backend and frontend dependencies
  seed     Seed deterministic stub data or an explicit real provider/model
  start    Migrate, then supervise the API and worker (optionally the UI)
  check    Run the same repository quality surfaces used by CI

Global options (accepted before or after the command):
  --env-file PATH   Environment file to load (default: backend/.env)
  --no-env          Do not load an environment file
  -h, --help        Show this help

Run "backend/scripts/dev.sh <command> --help" for command-specific options.
Environment files are optional unless --env-file is passed explicitly.
EOF
}

command_usage() {
  case "$1" in
    doctor) cat <<'EOF'
Usage: backend/scripts/dev.sh doctor

Checks Bash, uv, Python, Node/npm, lockfiles, and environment-file permissions.
This command does not install dependencies, migrate a database, or start services.
EOF
      ;;
    setup) cat <<'EOF'
Usage: backend/scripts/dev.sh setup [--backend-only | --frontend-only]

Installs locked development dependencies with:
  backend:  uv sync --all-extras --dev --locked
  frontend: npm ci
EOF
      ;;
    seed) cat <<'EOF'
Usage:
  backend/scripts/dev.sh seed --stub
  backend/scripts/dev.sh seed --provider NAME --model MODEL [options]

Options:
  --stub               Use the deterministic reasoner and dry-run agent
  --provider NAME      openai | openrouter | anthropic | gemini | local
  --model MODEL        Provider model identifier (never defaulted)
  --base-url URL       Override the provider preset endpoint
  --api-key-env NAME   Variable containing the key to import once

Real seeding requires both --provider and --model. Keys are never accepted as
arguments; put the key in the named environment variable or the env file.
EOF
      ;;
    start) cat <<'EOF'
Usage: backend/scripts/dev.sh start [options]

Options:
  --host HOST              API bind host (default: 127.0.0.1)
  --port PORT              API port (default: 8000)
  --worker-id ID           Worker identity (default: worker-1)
  --poll-seconds SECONDS   Worker poll interval (default: 1.0)
  --lease-seconds SECONDS  Plan lease duration (default: 300)
  --no-migrate             Skip the database upgrade (explicit opt-out)
  --frontend               Also supervise the Vite development server
  --frontend-host HOST     Vite bind host (default: 127.0.0.1)
  --frontend-port PORT     Vite port (default: 5173)

If any supervised process exits, all remaining processes are stopped and the
exiting process's status is returned. SIGINT/SIGTERM are forwarded by cleanup.
EOF
      ;;
    check) cat <<'EOF'
Usage: backend/scripts/dev.sh check [--backend-only | --frontend-only]

The full check validates the Codex plugin, runs backend make check, builds the
frontend, regenerates OpenAPI/types, and fails if generated files drift.
EOF
      ;;
    *) die "unknown command '$1'" ;;
  esac
}

require_command() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }
check_file() { [[ -f "$1" ]] || die "required file not found: $1"; }

load_environment() {
  [[ "$LOAD_ENV" == true ]] || return 0
  if [[ ! -f "$ENV_FILE" ]]; then
    [[ "${ENV_FILE_EXPLICIT:-false}" != true ]] || die "environment file not found: $ENV_FILE"
    note "environment file not found; continuing with process environment: $ENV_FILE"
    return 0
  fi
  # env.example defines shell-compatible assignments. A selected env file is
  # trusted local configuration; values are inherited but never printed.
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  note "loaded environment from $ENV_FILE"
}

validate_port() {
  [[ "$2" =~ ^[0-9]+$ ]] || die "$1 must be an integer"
  local value=$((10#$2))
  (( value >= 1 && value <= 65535 )) || die "$1 must be between 1 and 65535"
}
validate_positive_integer() { [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "$1 must be a positive integer"; }
validate_positive_number() {
  [[ "$2" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] || die "$1 must be a positive number"
  [[ "$2" =~ [1-9] ]] || die "$1 must be greater than zero"
}

run_doctor() {
  if [[ $# -gt 0 ]]; then
    [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { command_usage doctor; return; }
    die "doctor accepts no arguments"
  fi
  require_command bash
  (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3) )) || die "Bash 4.3 or newer is required"
  require_command uv
  require_command git
  require_command python
  require_command node
  require_command npm
  check_file "$BACKEND_DIR/uv.lock"
  check_file "$FRONTEND_DIR/package-lock.json"
  if [[ "$LOAD_ENV" == true && -e "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" && -r "$ENV_FILE" ]] || die "environment file is not a readable regular file: $ENV_FILE"
    if command -v stat >/dev/null 2>&1; then
      local mode
      mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)"
      if [[ -n "$mode" && $(( 8#$mode & 077 )) -ne 0 ]]; then
        printf 'warning: %s is group/world accessible (mode %s); use chmod 600 for secrets\n' "$ENV_FILE" "$mode" >&2
      fi
    fi
  elif [[ "${ENV_FILE_EXPLICIT:-false}" == true ]]; then
    die "environment file not found: $ENV_FILE"
  fi
  note "doctor passed (repo=$REPO_ROOT)"
}

select_surfaces() {
  RUN_BACKEND=true
  RUN_FRONTEND=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --backend-only) RUN_FRONTEND=false ;;
      --frontend-only) RUN_BACKEND=false ;;
      -h|--help) return 10 ;;
      *) die "unknown option: $1" ;;
    esac
    shift
  done
  [[ "$RUN_BACKEND" == true || "$RUN_FRONTEND" == true ]] || die "--backend-only and --frontend-only are mutually exclusive"
}

run_setup() {
  if select_surfaces "$@"; then :; else command_usage setup; return; fi
  if [[ "$RUN_BACKEND" == true ]]; then
    require_command uv
    note "installing locked backend dependencies"
    (cd "$BACKEND_DIR" && uv sync --all-extras --dev --locked)
  fi
  if [[ "$RUN_FRONTEND" == true ]]; then
    require_command npm
    note "installing locked frontend dependencies"
    (cd "$FRONTEND_DIR" && npm ci)
  fi
}

run_seed() {
  local mode="" provider="" model="" base_url="" api_key_env=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stub) [[ -z "$mode" ]] || die "choose either --stub or --provider"; mode=stub ;;
      --provider) [[ $# -ge 2 ]] || die "--provider requires a value"; [[ -z "$mode" || "$mode" == real ]] || die "choose either --stub or --provider"; mode=real; provider="$2"; shift ;;
      --model) [[ $# -ge 2 ]] || die "--model requires a value"; model="$2"; shift ;;
      --base-url) [[ $# -ge 2 ]] || die "--base-url requires a value"; base_url="$2"; shift ;;
      --api-key-env) [[ $# -ge 2 ]] || die "--api-key-env requires a value"; api_key_env="$2"; shift ;;
      -h|--help) command_usage seed; return ;;
      *) die "unknown seed option: $1" ;;
    esac
    shift
  done
  [[ -n "$mode" ]] || die "seed requires --stub or --provider NAME --model MODEL"
  if [[ "$mode" == stub ]]; then
    [[ -z "$model$base_url$api_key_env" ]] || die "provider options cannot be combined with --stub"
  else
    [[ -n "$provider" && -n "$model" ]] || die "real seeding requires --provider and --model"
    case "$provider" in openai|openrouter|anthropic|gemini|local) ;; *) die "unsupported provider: $provider" ;; esac
  fi
  require_command uv
  load_environment
  local args=(seed demo)
  if [[ "$mode" == stub ]]; then
    args+=(--stub)
  else
    args+=(--provider "$provider" --model "$model")
    [[ -z "$base_url" ]] || args+=(--base-url "$base_url")
    [[ -z "$api_key_env" ]] || args+=(--api-key-env "$api_key_env")
  fi
  note "seeding $mode development configuration"
  (cd "$BACKEND_DIR" && uv run orchestrate "${args[@]}")
}

CHILD_PIDS=()
cleanup_children() {
  local pid
  trap - EXIT INT TERM
  for pid in "${CHILD_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${CHILD_PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
}

run_start() {
  local host="${DEV_API_HOST:-127.0.0.1}" port="${DEV_API_PORT:-8000}"
  local worker_id="${DEV_WORKER_ID:-worker-1}" poll_seconds="${DEV_POLL_SECONDS:-1.0}"
  local lease_seconds="${DEV_LEASE_SECONDS:-300}" migrate=true frontend=false
  local frontend_host="${DEV_FRONTEND_HOST:-127.0.0.1}" frontend_port="${DEV_FRONTEND_PORT:-5173}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host) [[ $# -ge 2 ]] || die "--host requires a value"; host="$2"; shift ;;
      --port) [[ $# -ge 2 ]] || die "--port requires a value"; port="$2"; shift ;;
      --worker-id) [[ $# -ge 2 ]] || die "--worker-id requires a value"; worker_id="$2"; shift ;;
      --poll-seconds) [[ $# -ge 2 ]] || die "--poll-seconds requires a value"; poll_seconds="$2"; shift ;;
      --lease-seconds) [[ $# -ge 2 ]] || die "--lease-seconds requires a value"; lease_seconds="$2"; shift ;;
      --no-migrate) migrate=false ;;
      --frontend) frontend=true ;;
      --frontend-host) [[ $# -ge 2 ]] || die "--frontend-host requires a value"; frontend_host="$2"; shift ;;
      --frontend-port) [[ $# -ge 2 ]] || die "--frontend-port requires a value"; frontend_port="$2"; shift ;;
      -h|--help) command_usage start; return ;;
      *) die "unknown start option: $1" ;;
    esac
    shift
  done
  [[ -n "$host" && -n "$worker_id" && -n "$frontend_host" ]] || die "hosts and worker id cannot be empty"
  validate_port --port "$port"
  validate_port --frontend-port "$frontend_port"
  validate_positive_number --poll-seconds "$poll_seconds"
  validate_positive_integer --lease-seconds "$lease_seconds"
  require_command uv
  [[ "$frontend" == false ]] || require_command npm
  load_environment
  if [[ "$migrate" == true ]]; then
    note "migrating the development database to head"
    (cd "$BACKEND_DIR" && uv run orchestrate db upgrade)
  else
    note "database migration skipped by explicit --no-migrate"
  fi
  trap cleanup_children EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  note "starting API at http://$host:$port"
  (cd "$BACKEND_DIR" && exec uv run orchestrate api start --host "$host" --port "$port") & CHILD_PIDS+=("$!")
  note "starting worker $worker_id (poll=${poll_seconds}s, lease=${lease_seconds}s)"
  (cd "$BACKEND_DIR" && exec uv run orchestrate worker start --worker-id "$worker_id" --poll-seconds "$poll_seconds" --lease-seconds "$lease_seconds") & CHILD_PIDS+=("$!")
  if [[ "$frontend" == true ]]; then
    note "starting frontend at http://$frontend_host:$frontend_port"
    (cd "$FRONTEND_DIR" && exec npm run dev -- --host "$frontend_host" --port "$frontend_port") & CHILD_PIDS+=("$!")
  fi
  local status
  set +e
  wait -n "${CHILD_PIDS[@]}"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    note "a supervised process exited normally; stopping the remaining processes"
  else
    printf 'error: a supervised process exited with status %s; stopping the remaining processes\n' "$status" >&2
  fi
  return "$status"
}

run_check() {
  if select_surfaces "$@"; then :; else command_usage check; return; fi
  if [[ "$RUN_BACKEND" == true && "$RUN_FRONTEND" == true ]]; then
    require_command git
    require_command python
    note "validating repository-aware Codex plugin"
    (cd "$REPO_ROOT" && python plugins/agent-orchestrator-codex/scripts/validate.py)
  fi
  if [[ "$RUN_BACKEND" == true ]]; then
    require_command uv
    note "running backend quality gate"
    (cd "$BACKEND_DIR" && uv run make check)
  fi
  if [[ "$RUN_FRONTEND" == true ]]; then
    require_command npm
    note "building frontend"
    (cd "$FRONTEND_DIR" && npm run build)
    if [[ "$RUN_BACKEND" == true ]]; then
      note "regenerating OpenAPI client and checking for drift"
      local generated_before generated_after
      generated_before="$(git -C "$REPO_ROOT" diff HEAD -- frontend/openapi.json frontend/src/types/generated | git hash-object --stdin)"
      (cd "$FRONTEND_DIR" && npm run generate:api)
      generated_after="$(git -C "$REPO_ROOT" diff HEAD -- frontend/openapi.json frontend/src/types/generated | git hash-object --stdin)"
      [[ "$generated_before" == "$generated_after" ]] || die "generated API artifacts changed; commit the regenerated frontend files"
    fi
  fi
}

# Global environment options may appear on either side of the command.
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) [[ $# -ge 2 ]] || die "--env-file requires a path"; ENV_FILE="$2"; ENV_FILE_EXPLICIT=true; shift 2 ;;
    --no-env) LOAD_ENV=false; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
set -- "${ARGS[@]}"

COMMAND="${1:-}"
if [[ -z "$COMMAND" || "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then usage; exit 0; fi
shift
case "$COMMAND" in
  doctor) run_doctor "$@" ;;
  setup) run_setup "$@" ;;
  seed) run_seed "$@" ;;
  start) run_start "$@" ;;
  check) run_check "$@" ;;
  *) die "unknown command '$COMMAND' (run with --help)" ;;
esac
