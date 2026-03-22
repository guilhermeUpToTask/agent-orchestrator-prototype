"""
tests/unit/infra/test_project_state_adapter.py — Decision CRUD, supersession,
filtering by domain/status — for both in-memory and filesystem adapters.
"""
from __future__ import annotations

import pytest

from src.domain.ports.project_state import DecisionEntry
from src.infra.fs.project_state_adapter import (
    FilesystemProjectStateAdapter,
    InMemoryProjectStateAdapter,
)


# ---------------------------------------------------------------------------
# Fixtures — parametrised over both adapter implementations
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "filesystem"])
def adapter(request, tmp_path):
    if request.param == "memory":
        return InMemoryProjectStateAdapter()
    return FilesystemProjectStateAdapter(tmp_path / "state")


def _decision(
    id: str = "jwt-auth",
    domain: str = "authentication",
    feature_tag: str = "auth",
    status: str = "active",
    content: str = "Use JWT for stateless auth.",
) -> DecisionEntry:
    return DecisionEntry(
        id=id,
        date="2026-03-22",
        status=status,
        domain=domain,
        feature_tag=feature_tag,
        content=content,
    )


# ---------------------------------------------------------------------------
# Basic free-form state CRUD
# ---------------------------------------------------------------------------

def test_read_state_missing_key_returns_none(adapter):
    assert adapter.read_state("nonexistent") is None


def test_write_and_read_state(adapter):
    adapter.write_state("decisions", "## My decisions")
    assert adapter.read_state("decisions") == "## My decisions"


def test_list_keys(adapter):
    adapter.write_state("alpha", "a")
    adapter.write_state("beta", "b")
    keys = adapter.list_keys()
    assert "alpha" in keys
    assert "beta" in keys


def test_delete_state_existing(adapter):
    adapter.write_state("to-delete", "bye")
    assert adapter.delete_state("to-delete") is True
    assert adapter.read_state("to-delete") is None


def test_delete_state_missing(adapter):
    assert adapter.delete_state("ghost") is False


# ---------------------------------------------------------------------------
# write_decision / list_decisions
# ---------------------------------------------------------------------------

def test_write_and_list_decision(adapter):
    entry = _decision()
    adapter.write_decision(entry)
    results = adapter.list_decisions()
    assert len(results) == 1
    assert results[0].id == "jwt-auth"
    assert results[0].status == "active"
    assert results[0].domain == "authentication"


def test_list_decisions_default_active_only(adapter):
    adapter.write_decision(_decision("d1", status="active"))
    adapter.write_decision(_decision("d2", status="superseded"))
    active = adapter.list_decisions()
    assert all(d.status == "active" for d in active)
    assert len(active) == 1


def test_list_decisions_filter_by_domain(adapter):
    adapter.write_decision(_decision("jwt-auth", domain="authentication"))
    adapter.write_decision(_decision("postgres-main", domain="infra"))
    auth = adapter.list_decisions(domain="authentication")
    assert len(auth) == 1
    assert auth[0].id == "jwt-auth"


def test_list_decisions_all_statuses(adapter):
    adapter.write_decision(_decision("d1", status="active"))
    adapter.write_decision(_decision("d2", status="superseded"))
    all_decisions = adapter.list_decisions(status=None)
    assert len(all_decisions) == 2


def test_overwrite_decision(adapter):
    adapter.write_decision(_decision(content="v1"))
    adapter.write_decision(_decision(content="v2"))
    results = adapter.list_decisions()
    assert results[0].content == "v2"


# ---------------------------------------------------------------------------
# supersede_decision
# ---------------------------------------------------------------------------

def test_supersede_decision_marks_as_superseded(adapter):
    adapter.write_decision(_decision("jwt-auth"))
    result = adapter.supersede_decision("jwt-auth", "oauth2", "Moved to OAuth2")
    assert result is True
    decisions = adapter.list_decisions(status=None)
    superseded = next(d for d in decisions if d.id == "jwt-auth")
    assert superseded.status == "superseded"
    assert superseded.superseded_by == "oauth2"


def test_supersede_decision_appends_reason(adapter):
    adapter.write_decision(_decision("jwt-auth"))
    adapter.supersede_decision("jwt-auth", "oauth2", "Moved to OAuth2")
    all_d = adapter.list_decisions(status=None)
    d = next(x for x in all_d if x.id == "jwt-auth")
    assert "oauth2" in d.content.lower() or "oauth2" in (d.superseded_by or "")


def test_supersede_nonexistent_returns_false(adapter):
    result = adapter.supersede_decision("ghost", "other", "reason")
    assert result is False


def test_superseded_not_in_default_list(adapter):
    adapter.write_decision(_decision("jwt-auth"))
    adapter.supersede_decision("jwt-auth", "oauth2", "upgrade")
    active = adapter.list_decisions()
    assert not any(d.id == "jwt-auth" for d in active)


# ---------------------------------------------------------------------------
# Multi-domain / multi-status scenarios
# ---------------------------------------------------------------------------

def test_mixed_decisions_filtering(adapter):
    adapter.write_decision(_decision("d-auth", domain="authentication", feature_tag="auth"))
    adapter.write_decision(_decision("d-infra", domain="infra", feature_tag=""))
    adapter.write_decision(_decision("d-old", domain="authentication", status="superseded"))

    auth_active = adapter.list_decisions(domain="authentication", status="active")
    assert len(auth_active) == 1
    assert auth_active[0].id == "d-auth"

    infra_active = adapter.list_decisions(domain="infra")
    assert len(infra_active) == 1
    assert infra_active[0].id == "d-infra"


# ---------------------------------------------------------------------------
# Filesystem adapter round-trip (content integrity)
# ---------------------------------------------------------------------------

def test_filesystem_roundtrip_preserves_all_fields(tmp_path):
    adapter = FilesystemProjectStateAdapter(tmp_path / "state")
    entry = DecisionEntry(
        id="use-postgres",
        date="2026-01-15",
        status="active",
        domain="infra",
        feature_tag="data",
        content="# Use PostgreSQL\n\nWe chose PostgreSQL for ACID compliance.",
    )
    adapter.write_decision(entry)
    results = adapter.list_decisions(status=None)
    assert len(results) == 1
    r = results[0]
    assert r.id == "use-postgres"
    assert r.date == "2026-01-15"
    assert r.domain == "infra"
    assert r.feature_tag == "data"
    assert "PostgreSQL" in r.content
