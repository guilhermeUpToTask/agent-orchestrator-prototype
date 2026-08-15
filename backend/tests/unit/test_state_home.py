"""Where state lives, and what the CLI is called.

Both were changed by the Phase 10B rename, and both are the kind of thing that
fails silently rather than loudly: a wrong home directory looks exactly like a
working fresh install, and a missing entry point is only discovered by the first
person to install the wheel.

There is deliberately **no** compatibility with the pre-rename names. The
project has never been published, so the only installs that predated the rename
were the maintainer's own, and those were migrated rather than accommodated
(decision 65).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.state_home import resolve_home

_LEGACY_NAMES = (
    "ORCHESTRATOR_HOME",
    "ORCHESTRATOR_MASTER_KEY",
    "ORCHESTRATOR_API_TOKEN",
    "ORCHESTRATOR_DB_URL",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXIS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_a_fresh_install_uses_the_new_home(home) -> None:
    assert resolve_home() == home / ".praxis"


def test_an_explicit_home_wins(home, monkeypatch) -> None:
    monkeypatch.setenv("PRAXIS_HOME", str(home / "elsewhere"))

    assert resolve_home() == home / "elsewhere"


def test_the_container_resolves_the_same_home(home) -> None:
    """The composition root and the CLI runner must not disagree about this."""
    assert AppContainer.from_env().orchestrator_home == home / ".praxis"


@pytest.mark.parametrize("legacy", _LEGACY_NAMES)
def test_a_pre_rename_variable_is_ignored(home, monkeypatch, legacy) -> None:
    """Not read, not warned about, not special-cased.

    Asserted rather than assumed because the alias layer that used to read
    these was deleted deliberately, and a half-removed alias — one call site
    still falling back — is worse than either having it or not.
    """
    monkeypatch.setenv(legacy, str(home / "should-be-ignored"))

    assert resolve_home() == home / ".praxis"


def test_praxis_is_the_only_entry_point() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]

    assert scripts == {"praxis": "praxis_orchestrator.infra.cli.main:cli"}
