"""
tests/unit/domain/test_capability.py — CapabilityTag value object.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.domain.value_objects.capability import (
    CapabilityTag,
    normalize_capability,
)


class _Holder(BaseModel):
    tag: CapabilityTag


class TestNormalizeCapability:
    def test_lowercases_and_trims(self):
        assert normalize_capability("  CODE:Backend ") == "code:backend"

    def test_maps_legacy_alias(self):
        assert normalize_capability("coding") == "code:backend"

    def test_accepts_single_and_namespaced(self):
        assert normalize_capability("review") == "review"
        assert normalize_capability("test:write") == "test:write"
        assert normalize_capability("backend_dev") == "backend_dev"

    @pytest.mark.parametrize("bad", ["bad tag", "Has Space", "weird!", "", ":leading", "a:"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            normalize_capability(bad)


class TestCapabilityTagField:
    def test_field_normalizes_on_construction(self):
        assert _Holder(tag="coding").tag == "code:backend"

    def test_field_rejects_malformed(self):
        with pytest.raises(ValueError):
            _Holder(tag="not valid!")
