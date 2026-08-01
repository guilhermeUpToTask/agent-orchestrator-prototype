# first-cycle-v1 target repository

Disposable. Materialized outside the monorepo by
`fixtures/first-cycle-v1/scripts/materialize.sh` and reset to the
`first-cycle-v1-seed` tag between runs.

`src/first_cycle/slug.py` raises `NotImplementedError` on purpose and `tests/`
is empty on purpose: the run is only meaningful if the agent authors the test
FIRST and the implementation second.
