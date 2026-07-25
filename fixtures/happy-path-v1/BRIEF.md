# Happy-path v1 — plan brief (locked)

Paste the block below **verbatim** as the project plan brief. Do not paraphrase
between runs in the same series; wording drift makes runs incomparable.

---

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
