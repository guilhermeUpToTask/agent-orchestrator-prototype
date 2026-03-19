"""
src/cli.py — Backward-compatibility shim.

The CLI has moved to src/infra/cli/main.py.
This shim keeps `python -m src.cli` working during the transition.

New canonical entry point:
  python -m src.infra.cli.main
"""
from src.infra.cli.main import cli  # noqa: F401

if __name__ == "__main__":
    cli()
