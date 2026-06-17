"""
tests/unit/infra/fs/test_capability_registry.py — JsonCapabilityRegistry.
"""
from __future__ import annotations

from src.infra.fs.capability_registry import JsonCapabilityRegistry


def _registry(tmp_path):
    return JsonCapabilityRegistry(tmp_path / "capabilities" / "registry.json")


def test_add_list_exists_remove_roundtrip(tmp_path):
    reg = _registry(tmp_path)
    reg.add("code:backend")
    reg.add("test:write")
    assert reg.list_tags() == ["code:backend", "test:write"]
    assert reg.exists("code:backend")
    reg.remove("code:backend")
    assert not reg.exists("code:backend")


def test_add_normalizes_and_is_idempotent(tmp_path):
    reg = _registry(tmp_path)
    reg.add("coding")          # legacy alias → code:backend
    reg.add("  CODE:Backend ")  # same tag, different spelling
    assert reg.list_tags() == ["code:backend"]


def test_exists_false_for_malformed(tmp_path):
    reg = _registry(tmp_path)
    assert reg.exists("not valid!") is False


def test_ensure_defaults_adds_missing_without_clobbering(tmp_path):
    reg = _registry(tmp_path)
    reg.add("ml:training")
    reg.ensure_defaults(["code:backend", "review"])
    assert set(reg.list_tags()) == {"ml:training", "code:backend", "review"}


def test_registry_reads_fresh_across_instances(tmp_path):
    path = tmp_path / "capabilities" / "registry.json"
    JsonCapabilityRegistry(path).add("code:frontend")
    # A separate instance (e.g. another process) sees it immediately.
    assert JsonCapabilityRegistry(path).exists("code:frontend")
