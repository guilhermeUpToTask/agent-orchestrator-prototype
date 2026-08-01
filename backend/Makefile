.PHONY: check test lint typecheck

# Gate for every change: tests + lint + type ratchet.
check: lint typecheck test

test:
	pytest -q

lint:
	ruff check agent_orchestrator tests

# Zero errors under the declared config (strict for agent_orchestrator/domain
# and agent_orchestrator/app; documented relaxations for adapter layers in
# pyproject.toml). Tighten the overrides, never loosen them.
typecheck:
	mypy agent_orchestrator
