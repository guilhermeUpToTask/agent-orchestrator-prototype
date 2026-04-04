"""
src/infra/cli/error_handler.py — Centralised CLI error handling.

Policy:
  - All user-facing errors print to stderr with a ✗ prefix
  - All warnings print to stderr with a ⚠ prefix
  - All successes print to stdout with a ✓ prefix
  - sys.exit(1) is always called after a fatal error
  - Domain errors (KeyError, ValueError, DomainError) are caught
    at the command level and routed here
  - Unexpected errors are logged via structlog and printed
    with a generic message so the user always gets feedback

Usage:
    from src.infra.cli.error_handler import err, warn, ok, die, catch_domain_errors

    @task_group.command("retry")
    @click.argument("task_id")
    @catch_domain_errors
    def task_retry(task_id):
        result = usecase.execute(task_id)
        ok(f"Task {task_id} requeued")
"""

from __future__ import annotations

import sys
import functools
from typing import Callable, TypeVar, cast

import click
import structlog

from src.domain.errors import DomainError
from src.infra.settings.models import ConfigurationError


log = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable)


# ---------------------------------------------------------------------------
# Output helpers — single source of truth for CLI formatting
# ---------------------------------------------------------------------------


def ok(message: str) -> None:
    """Print a success line to stdout."""
    click.echo(f"✓  {message}")


def warn(message: str) -> None:
    """Print a warning line to stderr."""
    click.echo(f"⚠  {message}", err=True)


def err(message: str) -> None:
    """Print an error line to stderr."""
    click.echo(f"✗  {message}", err=True)


def die(message: str, code: int = 1) -> None:
    """Print an error and exit with the given code."""
    err(message)
    sys.exit(code)


def info(message: str) -> None:
    """Print a neutral info line to stdout."""
    click.echo(f"   {message}")


# ---------------------------------------------------------------------------
# Decorator — catch domain errors at the command boundary
# ---------------------------------------------------------------------------
def catch_domain_errors(fn: F) -> F:
    """
    Decorator for Click command functions.
    Catches the standard domain exception types and converts them to
    consistent CLI error output + sys.exit(1).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except click.Abort:
            click.echo()
            raise
        except click.exceptions.Exit:
            raise
        except KeyError as exc:
            die(f"Not found: {exc.args[0] if exc.args else exc}")

        # Add ConfigurationError to the clean catch block
        except (ValueError, DomainError, ConfigurationError) as exc:
            die(str(exc))

        except Exception as exc:
            log.exception("cli.unexpected_error", error=str(exc))
            die(f"Unexpected error: {exc}")

    return cast(F, wrapper)
