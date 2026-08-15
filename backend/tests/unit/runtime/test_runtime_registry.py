"""One registry is the single source of truth about runtimes.

Adding `codex` on 2026-08-09 required coordinated edits in FOUR files: the
runner class, `RUNTIME_TYPES`, the dispatch branch, and the dependency probe.
Nothing failed if one was forgotten — the runtime simply behaved oddly in
whichever surface was missed. These tests fail instead.
"""

from __future__ import annotations

from praxis_orchestrator.infra.runtime.registry import RUNTIME_REGISTRY, runtime_names
from praxis_orchestrator.infra.runtime.dependency_checker import RUNTIME_DEFINITIONS
from praxis_orchestrator.infra.runtime.factory import RUNTIME_TYPES


def test_the_registry_is_the_only_list_of_runtime_names() -> None:
    """`RUNTIME_TYPES` validates what an operator may store on an AgentSpec. If
    it and the registry can disagree, a runtime is registerable but not
    dispatchable, or dispatchable but rejected at write time."""
    assert set(RUNTIME_TYPES) == set(runtime_names())


def test_every_runtime_needing_a_binary_is_probed() -> None:
    """`/api/runner/status` reports binary probes. A runtime missing from the
    probe table looks installed when it is not — which is exactly the state
    that produces an AUTH_ERROR three layers later instead of at boot."""
    needing_binary = {d.name for d in RUNTIME_REGISTRY.values() if d.binary is not None}
    assert needing_binary == set(RUNTIME_DEFINITIONS)


def test_the_probe_table_agrees_with_the_registry_on_binary_and_hint() -> None:
    for name, (binary, hint) in RUNTIME_DEFINITIONS.items():
        descriptor = RUNTIME_REGISTRY[name]
        assert descriptor.binary == binary
        assert descriptor.install_hint == hint


def test_dry_run_needs_neither_a_binary_nor_a_key() -> None:
    """The one runtime that must never reach the secret store."""
    dry = RUNTIME_REGISTRY["dry-run"]
    assert dry.binary is None
    assert dry.needs_api_key is False


def test_codex_is_the_documented_exception_to_needing_a_key() -> None:
    """codex authenticates from CODEX_HOME. That asymmetry is real and must stay
    VISIBLE in the registry rather than hidden behind a default — it is why the
    factory resolves it before the secret store."""
    assert RUNTIME_REGISTRY["codex"].needs_api_key is False
    assert RUNTIME_REGISTRY["codex"].binary == "codex"
    for name in ("pi", "claude", "gemini"):
        assert RUNTIME_REGISTRY[name].needs_api_key is True
