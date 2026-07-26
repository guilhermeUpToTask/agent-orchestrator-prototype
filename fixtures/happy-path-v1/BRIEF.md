# Happy-path v1 — plan brief (locked)

The brief is the machine-readable file [`brief.txt`](brief.txt) — post that file
verbatim as the plan brief:

```bash
BRIEF="$(jq -Rs . < fixtures/happy-path-v1/brief.txt)"
./fixtures/happy-path-v1/scripts/api.sh POST /api/plans \
  "{\"brief\":$BRIEF,\"project_id\":\"$PROJECT_ID\"}"
```

Do not paraphrase `brief.txt` between runs in the same series; wording drift
makes runs incomparable. Bump to `happy-path-v2` if the brief has to change.

Its current text:

    Implement `greet(name: str) -> str` in `src/happy_path/greeter.py` so that
    `tests/test_greeter.py` passes with:

        greet("Ada") == "Hello, Ada!"

    Constraints:

    - Do not add HTTP, database, CLI, packaging extras, or new third-party dependencies.
    - Do not create extra modules unless strictly required for the existing test.
    - Do not refactor the project layout.
    - Verification is: `python -m pytest -q`
    - Prefer a **single goal** and the **smallest possible task set** (ideally one implementation task).

    Success means pytest is green and the default branch of the seed repo remains untouched.
