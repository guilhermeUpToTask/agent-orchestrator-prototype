# Contributing

Thanks for looking. This is a preview, and the rules below are what keep it
reviewable — most of them exist because breaking them already cost someone a day.

## Setup

```bash
cd backend
uv pip install -e .[dev]                 # or: pip install -e .[dev]
python -m agent_orchestrator.infra.cli.main db upgrade

cd ../frontend
npm install
```

## Before you open a PR

```bash
# backend
cd backend
ruff check agent_orchestrator tests --fix
mypy agent_orchestrator                                 # zero errors, no exclude list
pytest -m "not integration"              # fast
pytest -m integration                    # real SQLite, real git, TestClient
# Both run in parallel (`-n auto`). Add `-p no:xdist` when you need `--pdb`
# or readable output, and `make coverage` when you want the report.

# frontend
cd ../frontend
npx tsc --noEmit
npm run build
npm run test
```

Paid provider tests (`-m llm`) never run in normal CI and should not run in
yours by accident.

## The rules that matter

**Write the test first, and watch it fail.** A test that has never failed has
not been shown to test anything. Bug fixes get a regression test that reproduces
the bug before the fix exists.

**The domain is frozen.** `backend/agent_orchestrator/domain/` does not change without a
deliberate un-freeze recorded in
[`docs/decisions/decision-log.md`](docs/decisions/decision-log.md). There are
nineteen so far, each with a reason. If your change seems to need a twentieth,
say so in the PR before writing it.

**Respect the dependency rule.** `domain` → `app` → `infra` & `api`. The domain
imports nothing from the other layers; `app` talks to ports, never adapters.

**One error map.** Domain errors subclass `DomainError` with a stable `code`;
the API maps codes to HTTP statuses in exactly one table
(`agent_orchestrator/api/exceptions.py`). Do not scatter try/except returning responses in
routers.

**State and its events share one transaction.** A state change and its
`DomainEvent` are written in the same `with uow:` block. Side effects — agent
runs, LLM calls — happen outside transactions.

**A doc that contradicts the code is a bug in the doc**, fixed in the same PR.
Unimplemented ideas go to [`ROADMAP.md`](ROADMAP.md), never into
`docs/architecture/`. Verified defects go to
[`docs/architecture/known-issues.md`](docs/architecture/known-issues.md); when
you fix one, delete the entry and add the test that locks it.

**Never log secrets.** Keys live envelope-encrypted and are referenced by URI.
There is exactly one decryption point.

**No `print()` and no stdlib `logging`.** Structured logging via `structlog`,
with namespaced, action-oriented event names.

## Commits and PRs

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`) — the PR
title is linted and release notes are generated from it.

A PR description that says *what broke, how you reproduced it, and what now
proves it* is worth more than one that lists files changed.

## Running the real thing

`fixtures/first-cycle-v1/` drives a whole cycle over the API in one command and
then verifies the result against git rather than trusting it. Tier 0 (stub +
dry-run) is free and deterministic; Tier 1 needs a provider key and a CLI. Run
Tier 0 for any backend or control-plane change, and Tier 1 after touching
execution, reasoning, verification, runtime resolution, capacity, workspace or
publication.

Never mix tiers — see [docs/guides/tier-1.md](docs/guides/tier-1.md).

## Security

Please report vulnerabilities privately: see [SECURITY.md](SECURITY.md).
