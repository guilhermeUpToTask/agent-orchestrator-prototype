"""
tests/unit/infra/test_orchestrator_config_manager.py

Replaces the old OrchestratorConfigManager tests after config_manager.py was
deleted.  Now tests GlobalConfigStore — the single JSON persistence layer for
machine-level (global) config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.infra.settings import GlobalConfigStore
from src.infra.settings.defaults import MACHINE_DEFAULTS, MACHINE_MANAGED_KEYS


@pytest.fixture()
def store(tmp_path: Path) -> GlobalConfigStore:
    return GlobalConfigStore(home=tmp_path)


# ---------------------------------------------------------------------------
# existence
# ---------------------------------------------------------------------------

def test_exists_false_when_no_file(store):
    assert store.exists() is False


def test_exists_true_after_save(store):
    store.save({"project_name": "x"})
    assert store.exists() is True


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def test_load_returns_managed_defaults_when_missing(store):
    data = store.load_raw()
    for key in MACHINE_MANAGED_KEYS:
        assert key in data


def test_load_returns_written_values(store):
    store.save({"project_name": "my-proj", "redis_url": "redis://host:1234/1"})
    loaded = store.load_raw()
    assert loaded["project_name"] == "my-proj"
    assert loaded["redis_url"] == "redis://host:1234/1"


def test_load_fills_missing_keys_with_defaults(store):
    store.save({"project_name": "partial"})
    data = store.load_raw()
    assert data["redis_url"] == MACHINE_DEFAULTS["redis_url"]


def test_load_survives_corrupt_json(store):
    store.config_path.parent.mkdir(parents=True, exist_ok=True)
    store.config_path.write_text("{invalid json!!", encoding="utf-8")
    data = store.load_raw()
    for key in MACHINE_MANAGED_KEYS:
        assert key in data


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def test_save_creates_directory(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    s = GlobalConfigStore(home=deep)
    s.save({"project_name": "test"})
    assert s.config_path.exists()


def test_save_writes_valid_json(store):
    store.save({"project_name": "proj", "redis_url": "redis://x:6379/0"})
    on_disk = json.loads(store.config_path.read_text())
    assert on_disk["project_name"] == "proj"


# ---------------------------------------------------------------------------
# generate_defaults
# ---------------------------------------------------------------------------

def test_generate_defaults_writes_file(store):
    store.generate_defaults()
    assert store.exists()


def test_generate_defaults_returns_managed_keys(store):
    data = store.generate_defaults()
    assert "redis_url" in data


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_merges_values(store):
    store.save({"project_name": "old", "redis_url": "redis://old:6379/0"})
    store.update(project_name="new")
    data = store.load_raw()
    assert data["project_name"] == "new"
    assert data["redis_url"] == "redis://old:6379/0"


def test_update_ignores_none_values(store):
    store.save({"project_name": "keep"})
    store.update(project_name=None)
    data = store.load_raw()
    assert data["project_name"] == "keep"


def test_update_on_fresh_store_starts_from_defaults(store):
    store.update(redis_url="redis://new:6379/0")
    data = store.load_raw()
    assert data["redis_url"] == "redis://new:6379/0"


# ---------------------------------------------------------------------------
# secret exclusion — the critical regression guard
# ---------------------------------------------------------------------------

def test_save_strips_github_token(store):
    store.save({"project_name": "p", "github_token": "ghp_SECRET"})
    raw = json.loads(store.config_path.read_text())
    assert "github_token" not in raw


def test_save_strips_api_keys(store):
    store.save({
        "project_name": "p",
        "anthropic_api_key": "sk-ant",
        "gemini_api_key": "gm",
        "openrouter_api_key": "or",
    })
    raw = json.loads(store.config_path.read_text())
    for secret_key in ("anthropic_api_key", "gemini_api_key", "openrouter_api_key"):
        assert secret_key not in raw


def test_update_strips_secrets(store):
    store.update(project_name="p", github_token="ghp_SECRET")
    raw = json.loads(store.config_path.read_text())
    assert "github_token" not in raw
