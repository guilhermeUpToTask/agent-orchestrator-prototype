.PHONY: check test lint typecheck

# Gate for every change: tests + lint. Mypy is tracked separately until the
# type-health milestone lands (see docs/code-review-report.md §8 item 11).
check: lint test

test:
	pytest -q

lint:
	ruff check src tests

typecheck:
	mypy src
