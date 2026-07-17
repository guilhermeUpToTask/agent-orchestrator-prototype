# Local development workflow

The supported local workflow has one entry point:

```bash
backend/scripts/dev.sh --help
```

It resolves every path from its own location, so it works from any current
directory. It never defaults to a paid provider or model, never accepts a secret
value on the command line, and supervises long-running processes as one unit.
The older `start_api_and_worker.sh` and `reseed_openrouter_key.sh` names remain as
compatibility entry points; new automation should use `dev.sh`.

## First-time setup

Python 3.11 or 3.12, `uv`, Node.js, npm, Git, and Bash 4.3+ are required.

```bash
# Read-only preflight. Missing backend/.env is valid for stub/dry-run mode.
backend/scripts/dev.sh doctor

# Deterministic installs from both lockfiles.
backend/scripts/dev.sh setup

# Seed the no-key development defaults. Startup migrates the database.
backend/scripts/dev.sh seed --stub
```

`setup` accepts `--backend-only` and `--frontend-only` for focused work. It uses
`uv sync --all-extras --dev --locked` and `npm ci`; dependency resolution is not
silently changed by setup.

## Start the stack

The default starts the API and worker after applying all migrations:

```bash
backend/scripts/dev.sh start
```

Add the Vite UI to the same supervised group when wanted:

```bash
backend/scripts/dev.sh start --frontend
```

If the API, worker, or optional frontend exits, the launcher stops the remaining
children and returns the first process's exit status. `Ctrl-C` and termination
signals also clean up all children. This avoids orphan workers and partially live
stacks after a failure.

The launcher is parameterized without requiring script edits:

```bash
backend/scripts/dev.sh start \
  --host 0.0.0.0 \
  --port 8080 \
  --worker-id local-alice \
  --poll-seconds 0.5 \
  --lease-seconds 600 \
  --frontend \
  --frontend-port 5174
```

Ports, poll intervals, and lease durations are validated before migration or
startup. `--no-migrate` is available for an intentional exceptional case; the
safe default always upgrades the development database first.

The same values can be set for repeatable personal automation:

| Variable | Default | Equivalent option |
|---|---:|---|
| `DEV_API_HOST` | `127.0.0.1` | `--host` |
| `DEV_API_PORT` | `8000` | `--port` |
| `DEV_WORKER_ID` | `worker-1` | `--worker-id` |
| `DEV_POLL_SECONDS` | `1.0` | `--poll-seconds` |
| `DEV_LEASE_SECONDS` | `300` | `--lease-seconds` |
| `DEV_FRONTEND_HOST` | `127.0.0.1` | `--frontend-host` |
| `DEV_FRONTEND_PORT` | `5173` | `--frontend-port` |
| `DEV_ENV_FILE` | `backend/.env` | `--env-file` |

Explicit command-line options take precedence over these variables.

## Use a real provider deliberately

Copy and protect the documented environment template, then edit it locally:

```bash
cp backend/env.example backend/.env
chmod 600 backend/.env
```

The file is optional in stub mode. When selected, it is trusted local shell
configuration, loaded without printing values, and inherited by seed/API/worker
processes. Use `--no-env` to rely only on the calling process environment, or
`--env-file PATH` to select a different file. An explicitly selected missing
file is an error; an absent default `backend/.env` is not.

Real seeding always requires an explicit provider and model:

```bash
backend/scripts/dev.sh seed \
  --provider openrouter \
  --model anthropic/claude-sonnet-4-5 \
  --api-key-env OPENROUTER_API_KEY
```

The key value belongs in `OPENROUTER_API_KEY` (or the variable named by
`--api-key-env`), never in an argument. The CLI imports it once into the encrypted
catalog. `--base-url` is available for an intentional endpoint override; the
`local` provider requires one. No provider/model is embedded in the workflow, so
changing a model does not require changing tracked files.

## Quality gate and CI parity

Run focused checks while iterating and the full gate before opening a PR:

```bash
backend/scripts/dev.sh check --backend-only
backend/scripts/dev.sh check --frontend-only
backend/scripts/dev.sh check
```

The full command validates the repository Codex plugin, runs backend lint, typing,
and tests, builds the frontend, regenerates the OpenAPI client, and fails on
generated-file drift. The paid `pytest -m llm` smoke test is deliberately excluded
from this workflow and from normal CI.

CI parses every workflow shell helper and exercises its help surface. That smoke
gate catches broken quoting, dispatch, and compatibility-wrapper regressions
before a branch reaches runtime tests.

## Troubleshooting

- `doctor` warns when the selected environment file is group/world accessible.
- If `start` reports a migration failure, fix that error before using
  `--no-migrate`; skipping migrations normally turns a clear startup failure into
  a later SQLite schema error.
- If a real seed says a key variable is empty, export it or put it in the selected
  environment file. Do not pass the key itself as `--api-key-env`.
- Use `backend/scripts/dev.sh <command> --help` as the authoritative parameter
  reference. The raw `orchestrate` CLI remains available for single-process and
  operational tasks.
