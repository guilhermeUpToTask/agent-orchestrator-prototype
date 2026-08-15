.PHONY: check test test-serial coverage lint typecheck

# Gate for every change: tests + lint + type ratchet.
check: lint typecheck test

# Parallel by default (`-n auto` lives in pyproject addopts).
test:
	pytest -q

# Serial, for a bisect or a debugger session: xdist swallows `--pdb` and
# interleaves output from every worker.
test-serial:
	pytest -q -p no:xdist

# Coverage is opt-in rather than a tax on every run. Nothing gates on the
# number, so it belongs where someone is actually going to read it.
coverage:
	pytest -q --cov=praxis_orchestrator --cov-report=term-missing

lint:
	ruff check praxis_orchestrator tests

# Zero errors under the declared config (strict for praxis_orchestrator/domain
# and praxis_orchestrator/app; documented relaxations for adapter layers in
# pyproject.toml). Tighten the overrides, never loosen them.
typecheck:
	mypy praxis_orchestrator
