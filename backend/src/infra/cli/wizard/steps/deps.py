"""
src/infra/cli/wizard/steps/deps.py — Wizard Step 2: dependency check.

Also used by the `start` command — extracted here so both paths share
the same check-and-report logic.
"""
from __future__ import annotations

import click

from src.infra.dependency_checker import DependencyChecker, DependencyReport


def check_and_report(redis_url: str) -> bool:
    """
    Run all dependency checks, print a status table, and return True only
    if the minimum requirements (redis + git + one runtime) are met.
    """
    checker = DependencyChecker(redis_url=redis_url)
    report: DependencyReport = checker.run()

    click.echo()
    for r in report.results:
        icon = "  ✓" if r.ok else "  ✗"
        click.echo(f"{icon}  {r.name:<20} {r.message}")
        if not r.ok and r.install_hint:
            click.echo(f"              → {r.install_hint}")

    click.echo()

    if not report.redis_ok:
        click.echo("  Redis is required. Start it, then re-run the wizard.", err=True)
        return False
    if not report.git_ok:
        click.echo("  git is required. Install it, then re-run the wizard.", err=True)
        return False
    if not report.any_runtime_ok:
        click.echo(
            "  At least one agent runtime (gemini-cli, claude-code, or pi-mono)"
            " must be installed.",
            err=True,
        )
        return False

    click.echo("  All required dependencies satisfied ✓")
    return True


def print_dep_table(redis_url: str) -> DependencyReport:
    """
    Run checks and print the table. Returns the report for the caller to
    inspect `can_start`. Used by the `start` command.
    """
    checker = DependencyChecker(redis_url=redis_url)
    report: DependencyReport = checker.run()
    for r in report.results:
        icon = "✓" if r.ok else "✗"
        click.echo(f"  {icon}  {r.name}: {r.message}")
    return report
