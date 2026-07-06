# backend/docs

- **[INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)** — the frozen per-port contract handbook: exact method signatures, the version-CAS and lease SQL shapes, the API→use-case map, and the verification procedure. If the code and this guide disagree, fix the guide first, then implement. Contracts change only with a deliberate domain un-freeze.

System-level documentation (architecture with diagrams, decision log, legacy features, history) lives at the repo root: [`../../docs/`](../../docs/README.md). Two files that used to live here moved there with the 2026-07-06 docs refactor:

- `adr-concurrency-lease.md` → [`docs/decisions/adr-001-concurrency-lease.md`](../../docs/decisions/adr-001-concurrency-lease.md)
- `DESIGN_NOTES.md` → [`docs/decisions/domain-design-decisions.md`](../../docs/decisions/domain-design-decisions.md)

Per-layer READMEs sit next to the code: [`src/domain/`](../src/domain/README.md) · [`src/app/`](../src/app/README.md) · [`src/infra/`](../src/infra/README.md) · [`src/api/`](../src/api/README.md) · [`tests/`](../tests/README.md).
