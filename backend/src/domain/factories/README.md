# Factories

Pure construction — no I/O, no repository. The repository *calls* a factory to reconstruct;
a factory never calls the repository. Every entity gets the same two-method split:

- **`create(...)`** — build from zero (a new thing). Runs birth invariants (e.g. a plan must
  have a brief), generates the id via `identity.new_id()`, applies defaults.
- **`reconstruct(data)`** — rebuild from persisted state (a repo loading a row). Trusts the
  stored data was valid when saved; does **not** regenerate the id or re-apply defaults over
  real values. Validation of field types is Pydantic's job.

`identity.new_id()` is the single source of id generation, so the strategy (uuid now,
something else later) is centralized instead of scattered across call sites.

### On thin factories (e.g. `CapabilityFactory`)
For a simple reference entity the factory is nearly a passthrough, and you *could* call the
constructor directly. It's kept for two reasons: `create()` centralizes id generation, and
every entity following the same `create`/`reconstruct` shape is worth more than the few lines
saved. Inline it only if the entity never grows birth invariants — see
[`../../DESIGN_NOTES.md`](../../DESIGN_NOTES.md).
