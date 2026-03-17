"""
tests/unit/infra/test_orchestrator_config_manager.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.orchestrator_config_manager import (
    DEFAULTS,
    OrchestratorConfigManager,
)


@pytest.fixture()
def tmp_mgr(tmp_path: Path) -> OrchestratorConfigManager:
    return OrchestratorConfigManager(cwd=tmp_path)


# ── existence ─────────────────────────────────────────────────────────────────


def test_exists_false_when_no_file(tmp_mgr):
    assert tmp_mgr.exists() is False


def test_exists_true_after_save(tmp_mgr):
    tmp_mgr.save({"project_name": "x"})
    assert tmp_mgr.exists() is True


# ── load ──────────────────────────────────────────────────────────────────────


def test_load_returns_defaults_when_missing(tmp_mgr):
    data = tmp_mgr.load()
    assert data == dict(DEFAULTS)


def test_load_returns_written_values(tmp_mgr):
    payload = {
        "project_name": "my-proj",
        "redis_url": "redis://host:1234/1",
        "source_repo_url": None,
    }
    tmp_mgr.save(payload)
    loaded = tmp_mgr.load()
    assert loaded["project_name"] == "my-proj"
    assert loaded["redis_url"] == "redis://host:1234/1"


def test_load_fills_missing_keys_with_defaults(tmp_mgr):
    # Write only project_name; redis_url should be filled from DEFAULTS
    tmp_mgr.save({"project_name": "partial"})
    data = tmp_mgr.load()
    assert data["redis_url"] == DEFAULTS["redis_url"]


def test_load_survives_corrupt_json(tmp_mgr):
    tmp_mgr.config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_mgr.config_path.write_text("{invalid json!!", encoding="utf-8")
    data = tmp_mgr.load()
    assert data == dict(DEFAULTS)


# ── save ──────────────────────────────────────────────────────────────────────


def test_save_creates_directory(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    mgr = OrchestratorConfigManager(cwd=deep)
    mgr.save({"project_name": "test"})
    assert mgr.config_path.exists()


def test_save_writes_valid_json(tmp_mgr):
    tmp_mgr.save({"project_name": "proj", "redis_url": "redis://x:6379/0"})
    on_disk = json.loads(tmp_mgr.config_path.read_text())
    assert on_disk["project_name"] == "proj"


# ── generate_defaults ─────────────────────────────────────────────────────────


def test_generate_defaults_writes_and_returns_defaults(tmp_mgr):
    data = tmp_mgr.generate_defaults()
    assert data == dict(DEFAULTS)
    assert tmp_mgr.exists()
    reloaded = json.loads(tmp_mgr.config_path.read_text())
    assert reloaded["project_name"] == DEFAULTS["project_name"]


# ── update ────────────────────────────────────────────────────────────────────


def test_update_merges_values(tmp_mgr):
    tmp_mgr.save({"project_name": "old", "redis_url": "redis://old:6379/0"})
    tmp_mgr.update(project_name="new")
    data = tmp_mgr.load()
    assert data["project_name"] == "new"
    assert data["redis_url"] == "redis://old:6379/0"


def test_update_ignores_none_values(tmp_mgr):
    tmp_mgr.save({"project_name": "keep"})
    tmp_mgr.update(project_name=None)
    data = tmp_mgr.load()
    assert data["project_name"] == "keep"


def test_update_on_fresh_manager_starts_from_defaults(tmp_mgr):
    tmp_mgr.update(redis_url="redis://new:6379/0")
    data = tmp_mgr.load()
    assert data["redis_url"] == "redis://new:6379/0"
    # Defaults filled in
    assert data["project_name"] == DEFAULTS["project_name"]
