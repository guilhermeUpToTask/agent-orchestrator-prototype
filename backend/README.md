# agent-orchestrator

A **local-first, human-gated, verified multi-agent coding orchestrator** for
developers working on their own repositories.

It is *not* a fully autonomous software factory, a multi-tenant SaaS, a sandboxed
platform, or a replacement for an engineering team. Agent output is a
**candidate only**: protected tests, declared scope, branch integrity and
verification evidence are checked independently before anything is promoted.

## Install

```bash
pipx install agent-orchestrator     # or: uvx --from agent-orchestrator orchestrate
orchestrate serve
```

`serve` migrates the state directory (`~/.orchestrator` by default), starts the
API and a worker, and serves the console at <http://127.0.0.1:8000>. Open it and
follow **Settings → Get started**, which sequences the setup in dependency
order.

## The two tiers

| | Reasoner | Agent runtime | Cost |
|---|---|---|---|
| **Tier 0** | `stub` | `dry-run` | free, deterministic — exercises the whole lifecycle |
| **Tier 1** | `llm` | `real` | your provider's rates; free models work |

Never mix halves: a real reasoner verified by a dummy runner produces evidence
that means nothing.

## Requirements

- Python 3.11+
- Git
- For Tier 1: a provider API key, `ORCHESTRATOR_MASTER_KEY` (encrypts the key at
  rest), and the agent CLI you bind to (`pi`, `claude`) on `PATH`

## Security posture, in one line

Agent runtimes execute **unsandboxed**, as your user, against the repository you
point them at. See [SECURITY.md](https://github.com/guilhermeUpToTask/agent-orchestrator-prototype/blob/main/SECURITY.md).

## Documentation

Full architecture, decision log and operator walkthroughs live in the
[repository](https://github.com/guilhermeUpToTask/agent-orchestrator-prototype).

Licensed under Apache-2.0.
