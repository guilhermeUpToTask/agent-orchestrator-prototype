"""An install that predates the rename keeps working, and keeps its state.

Phase 10B renamed the project. Three of the things it renamed are not code —
they are state and configuration already sitting on machines:

    ORCHESTRATOR_MASTER_KEY   ~/.orchestrator
    ORCHESTRATOR_API_TOKEN    ORCHESTRATOR_HOME / ORCHESTRATOR_DB_URL

**The API token alias is the one that makes this non-negotiable.** `security.py`
treats an unset token as "open in local dev", so an upgrade that stopped reading
the old name would not raise, log, or fail a health check. It would silently
unguard the control plane, and the first sign would be a stranger's request
succeeding.

The state directory is the other half: `~/.orchestrator` holds every plan and
the encrypted secret store, and a new default that quietly starts empty beside
it looks exactly like a working fresh install.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.secret_ref import SecretRef
from praxis_orchestrator.infra.db.tables import Base
from praxis_orchestrator.infra.env_compat import env, resolve_home

pytestmark = pytest.mark.integration


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PRAXIS_HOME",
        "PRAXIS_MASTER_KEY",
        "PRAXIS_API_TOKEN",
        "PRAXIS_DB_URL",
        "ORCHESTRATOR_HOME",
        "ORCHESTRATOR_MASTER_KEY",
        "ORCHESTRATOR_API_TOKEN",
        "ORCHESTRATOR_DB_URL",
    ):
        monkeypatch.delenv(name, raising=False)


# --- the environment aliases -------------------------------------------------


@pytest.mark.parametrize(
    ("current", "legacy"),
    [
        ("PRAXIS_HOME", "ORCHESTRATOR_HOME"),
        ("PRAXIS_MASTER_KEY", "ORCHESTRATOR_MASTER_KEY"),
        ("PRAXIS_API_TOKEN", "ORCHESTRATOR_API_TOKEN"),
        ("PRAXIS_DB_URL", "ORCHESTRATOR_DB_URL"),
    ],
)
def test_the_pre_rename_variable_is_still_read(monkeypatch, current, legacy) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(legacy, "from-the-old-name")

    assert env(current) == "from-the-old-name"


def test_the_new_name_wins_when_both_are_set(monkeypatch) -> None:
    """An operator midway through migrating gets the value they just wrote."""
    _clear(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "old")
    monkeypatch.setenv("PRAXIS_API_TOKEN", "new")

    assert env("PRAXIS_API_TOKEN") == "new"


def test_an_unknown_name_has_no_alias(monkeypatch) -> None:
    _clear(monkeypatch)
    assert env("PRAXIS_NOT_A_REAL_SETTING", "fallback") == "fallback"


# --- the one that silently unguards the API ----------------------------------


@pytest.fixture
def legacy_token_client(tmp_path, monkeypatch):
    """A server configured the pre-rename way and nothing else."""
    _clear(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as client:
        yield client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_a_legacy_api_token_still_guards_the_control_plane(legacy_token_client) -> None:
    """The regression that would not have announced itself."""
    assert legacy_token_client.get("/api/providers").status_code == 401


def test_a_legacy_api_token_still_admits_the_right_caller(legacy_token_client) -> None:
    response = legacy_token_client.get(
        "/api/providers", headers={"X-API-Token": "sekrit"}
    )

    assert response.status_code == 200


def test_a_legacy_master_key_still_decrypts_a_stored_secret(tmp_path, monkeypatch) -> None:
    """Written under the old variable, read back after the rename."""
    _clear(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    ref = SecretRef.for_provider("provider-1")

    container.secret_store.put(ref, "sk-live-preserved")

    assert container.secret_store.resolve(ref).get_secret_value() == "sk-live-preserved"


# --- the state directory -----------------------------------------------------


def test_an_existing_legacy_home_is_adopted_in_place(tmp_path, monkeypatch) -> None:
    """NOT moved and NOT copied.

    A move breaks rolling back to the previous version; a copy duplicates the
    encrypted secret store, which is the one file that must never exist twice.
    """
    _clear(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    legacy = tmp_path / ".orchestrator"
    legacy.mkdir()
    (legacy / "orchestrator.db").write_text("the operator's plans", encoding="utf-8")

    resolved = resolve_home()

    assert resolved == legacy
    assert (legacy / "orchestrator.db").read_text(encoding="utf-8") == "the operator's plans"
    assert not (tmp_path / ".praxis").exists(), "adoption must not create the new one"


def test_the_new_home_wins_once_it_exists(tmp_path, monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".orchestrator").mkdir()
    (tmp_path / ".praxis").mkdir()

    assert resolve_home() == tmp_path / ".praxis"


def test_a_fresh_install_uses_the_new_home(tmp_path, monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert resolve_home() == tmp_path / ".praxis"


def test_an_explicit_home_beats_both(tmp_path, monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".orchestrator").mkdir()
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "elsewhere"))

    assert resolve_home() == tmp_path / "elsewhere"


def test_the_container_resolves_the_legacy_home(tmp_path, monkeypatch) -> None:
    """End to end: `AppContainer.from_env()` on a pre-rename machine points at
    the operator's real database rather than an empty new one."""
    _clear(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    legacy = tmp_path / ".orchestrator"
    legacy.mkdir()

    assert AppContainer.from_env().orchestrator_home == legacy


# --- readiness, which is the first thing a pre-rename install shows ----------


def test_readiness_sees_a_legacy_master_key(tmp_path, monkeypatch) -> None:
    """A false red here is worse than a silent one.

    `/api/readiness` is the first mile: an operator whose install predates the
    rename has a master key that the secret store reads perfectly well through
    the alias, so a `fail` saying it "is not set" sends them to generate a
    second one — and CLAUDE.md, the dev-VM README and this file all warn that a
    second key permanently orphans the encrypted store.
    """
    _clear(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.config_store.set("orchestrator", "reasoner.mode", "llm")

    with TestClient(create_app(container)) as client:
        body = client.get("/api/readiness").json()
    dependencies.set_container(None)  # type: ignore[arg-type]

    secrets = next(check for check in body["checks"] if check["name"] == "secrets")
    assert secrets["status"] == "ok", secrets["detail"]


# --- the command an operator already has in their scripts --------------------


def _declared_scripts() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]


def test_both_entry_points_ship() -> None:
    """`praxis` is the name; `orchestrate` is what existing scripts already call."""
    scripts = _declared_scripts()

    assert scripts["praxis"] == "praxis_orchestrator.infra.cli.main:cli"
    assert scripts["orchestrate"] == "praxis_orchestrator.infra.cli.main:legacy_cli"


def _run_legacy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from praxis_orchestrator.infra.cli.main import legacy_cli; legacy_cli()",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_legacy_entry_point_still_runs_the_command() -> None:
    result = _run_legacy("version", "--json")

    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {
        "version",
        "commit",
        "python",
        "platform",
        "executable",
    }


def test_the_deprecation_notice_stays_off_stdout() -> None:
    """`orchestrate plan show … | jq` is in this repository's own fixtures.

    A deprecation line on stdout would break every one of them, which is a
    worse outcome than the rename it is announcing.
    """
    result = _run_legacy("version", "--json")

    assert "renamed" not in result.stdout
    assert "renamed to `praxis`" in result.stderr
