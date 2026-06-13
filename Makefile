.PHONY: check test lint typecheck

# Gate for every change: tests + lint + type ratchet.
check: lint typecheck test

test:
	pytest -q

lint:
	ruff check src tests

# Zero errors under the declared config (strict for src/domain and
# src/app; documented per-module relaxations for adapter layers in
# pyproject.toml). Tighten the overrides, never loosen them.
typecheck:
	mypy src
