# Security

`praxis-orchestrator` is a **preview**. This document states what it does and
does not protect, plainly, so you can decide what to point it at. Nothing here
is aspirational: every claim describes code in this repository.

## Reporting a vulnerability

Use **[GitHub private security advisories](https://github.com/guilhermeUpToTask/praxis-orchestrator/security/advisories/new)**
rather than a public issue. Include a reproduction and the version or commit.

There is no SLA yet — this is a preview maintained by a small number of people.
You will get an acknowledgement, and a fix or an honest "not soon".

## The one thing to understand first

**Agent runtimes execute unsandboxed, as your user account.**

When `agent_runner.mode = real`, the orchestrator runs the CLI you bound the
agent to (`pi`, `claude`, …) as a subprocess with your environment, your
filesystem permissions, your git identity and your credentials. Those CLIs run a
model's output. There is **no container, no seccomp filter, no syscall
restriction, and no filesystem jail**. `praxis_orchestrator/infra/runtime/sandbox.py` supplies
`NoSandbox`, which passes the command through unchanged; its `probe()` reports
that plainly rather than claiming a healthy sandbox, and it is the deliberate
permanent fallback for hosts with no OS-level confinement — not a stub awaiting
deletion. A real confining adapter is roadmap work, not shipped.

What *is* enforced is narrower, and worth knowing precisely:

- work happens in a **git worktree on a task branch**, never on your default
  branch, and a failed attempt's worktree and branch are deleted;
- only **independently verified** work moves up the branch ladder — the
  orchestrator re-runs the task's verification commands and checks declared
  scope, protected test hashes and branch integrity before promoting;
- the repository's detected **default branch is never written** by plan work.

Those are correctness controls that also limit blast radius. They are not a
security boundary. A model that decides to run `curl … | sh` inside its worktree
is running it on your machine.

**Point it at repositories you can afford to lose, on a machine you can afford
to rebuild.** A disposable clone or a VM is the right posture for the preview.

## Control-plane authentication

`PRAXIS_API_TOKEN` guards **every** operation except `GET /health`:

- unset -> the API is **open** to anyone who can reach the port. The default
  bind is `127.0.0.1`, so that is local-only until you change it.
- set -> every request must present `Authorization: Bearer <token>` or
  `X-API-Token`.

The guard is applied once at mount (`praxis_orchestrator/api/server.py`) and a test parametrizes
it over the whole OpenAPI inventory, so a route added later is covered before it
is written.

One deliberate exception to the *mechanism*: `GET /api/events` also accepts
`?token=`, because `EventSource` cannot send headers. The token therefore appears
in a URL for that route. uvicorn's access log is disabled and the structured
request log records `url.path` only, so it is not written to disk by this
application — but it would appear in a proxy log if you put one in front.

Known limits, stated rather than hidden:

- one **shared** token, not per-user credentials, and no revocation beyond
  changing it;
- the comparison is `!=`, not constant-time;
- no rate limiting, no CSRF tokens, no account model. This is a single-operator
  local tool, not a multi-tenant service.

## Secrets

Provider API keys are **envelope-encrypted at rest** (Fernet) in the `secrets`
table, keyed by `PRAXIS_MASTER_KEY`.

- The key is read only in the composition root and decrypted at exactly one
  point (`secret_store.py::resolve`).
- Everything else — the catalog, the API, the logs, the read models — carries
  `api_key_ref` **URIs**, never plaintext. `POST /api/providers` accepts a key
  once and never echoes it back.
- Structured logging never writes secret material. If you find a log line that
  does, that is a vulnerability worth reporting.
- Lose `PRAXIS_MASTER_KEY` and the stored keys are unrecoverable; that is
  the intended failure mode.

### GitHub forge tokens

A project may bind a GitHub token so `open_pr` really opens a pull request
(P8.1). It is stored in the same envelope-encrypted `secrets` table, at
`secret://forge/<project_id>`, and no endpoint returns it.

- **Per project, not global.** Two projects can live on different accounts, and
  one credential spanning every project is the wrong blast radius here.
- **It needs write access** to that one repository — a fine-grained token with
  Contents and Pull requests write, or a classic `repo` scope. It is verified
  against that exact repository when you save it.
- **What it is used for, exhaustively:** pushing `cycle/<id>`, and one
  `POST /repos/{owner}/{repo}/pulls`. The port has no merge method and no way to
  name another branch. The default branch is never written.
- The token crosses into plaintext in one Authorization header and one push
  remote URL. Git echoes that URL in its stderr on failure, so the push scrubs
  the token out of any error it raises.
- Deleting the binding deletes the secret.

Note that the **agent CLIs receive a decrypted key in their environment** when
they run — `cli_runner.py` puts it in the child process env (e.g.
`ANTHROPIC_API_KEY`) because that is how those CLIs authenticate. Encryption at
rest does not change what a subprocess, or anything it spawns, can read.

## What the preview does not have

- no sandboxing or resource limits on agent processes;
- no *automatic* merging — the orchestrator opens a pull request and never
  merges one. `merge` still records an operation *you* performed. Forge
  publication itself now exists; see the section above for what it can reach;
- no multi-tenancy, no user accounts, no audit log of *who* did what (the event
  stream records what happened, not which human asked);
- no supply-chain verification of the models or CLIs you configure;
- no network egress controls.

## Supported surface

- Python 3.11+ on Linux and macOS. Windows is untested.
- SQLite state under `PRAXIS_HOME` (default `~/.praxis`). Back that
  directory up; it holds your plans, evidence and encrypted keys.
- Agent runtimes: `pi`, `claude` (each must be installed and authenticated by
  you). `gemini` is bindable but unexercised.

## Costs

Tier 1 spends real provider quota. The orchestrator retries on capacity failures
with wall-clock ceilings rather than unbounded attempt counts, and a provider
admission gate limits in-flight requests, but **it does not enforce a spend
cap**. Use a provider-side budget limit, or free models, for the preview.
