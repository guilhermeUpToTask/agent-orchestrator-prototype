#!/usr/bin/env bash
set -euo pipefail

set -a
source ../.env
set +a

api_pid=""
worker_pid=""

cleanup() {
  trap - EXIT INT TERM
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
  [[ -n "$worker_pid" ]] && kill "$worker_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && wait "$api_pid" 2>/dev/null || true
  [[ -n "$worker_pid" ]] && wait "$worker_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

#migration run in case of stale database schemas
uv run orchestrate db upgrade

uv run orchestrate api start &
api_pid=$!
uv run orchestrate worker start &
worker_pid=$!

wait -n "$api_pid" "$worker_pid"
