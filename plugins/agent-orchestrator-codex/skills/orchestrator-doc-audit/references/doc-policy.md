# Documentation policy

- Current system truth lives in code, tests, `README.md`, `CLAUDE.md`, and `docs/architecture/`.
- `ROADMAP.md` contains planned but unimplemented work.
- `docs/history/` is an audit trail, not current design guidance.
- `docs/legacy/` preserves intentionally removed pre-refactor features.
- A fixed known issue must disappear from `known-issues.md` and gain a regression test.
- Domain unfreezes require a numbered decision-log entry.
- CLI commands, config keys, environment variables, migration head, and API generation commands must be verified verbatim.
- Do not document direct router-to-SSE publication, Redis coordination, stored navigation cursors, or `AGENT_MODE`; those contradict current architecture.
