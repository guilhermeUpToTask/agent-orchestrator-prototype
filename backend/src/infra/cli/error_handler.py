"""
src/infra/cli/error_handler.py — centralised CLI error handling.

Policy:
  - user-facing errors -> stderr with a ✗ prefix, exit(1)
  - warnings -> stderr with ⚠; successes -> stdout with ✓
  - typed errors (DomainError / BaseAppException) print their message + code
  - unexpected errors are logged via structlog and printed generically so the
    user always gets feedback, never a bare traceback
"""

from __future__ import annotations

import functools
import sys
from typing import Callable, NoReturn, TypeVar, cast

import click
import structlog

from src.domain.errors import BaseAppException

log = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable)


def ok(message: str) -> None:
    click.echo(f"✓  {message}")


def warn(message: str) -> None:
    click.echo(f"⚠  {message}", err=True)


def err(message: str) -> None:
    click.echo(f"✗  {message}", err=True)


def die(message: str) -> NoReturn:
    err(message)
    sys.exit(1)


def catch_domain_errors(fn: F) -> F:
    """Route typed errors to stderr + exit(1); log the unexpected ones."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return fn(*args, **kwargs)
        except BaseAppException as exc:
            die(f"[{exc.code}] {exc.message}")
        except (KeyError, ValueError) as exc:
            die(str(exc))
        except Exception as exc:  # noqa: BLE001 — the CLI's last-resort net
            log.error("cli.unexpected_error", exc_info=exc)
            die(f"Unexpected error: {type(exc).__name__}: {exc}")

    return cast(F, wrapper)
