"""`reasoner.*` config must take effect without restarting the worker.

`AppContainer.reasoner` was resolved once and the worker captured the instance
into `PlanningHandler` at boot, so every `reasoner.*` key was boot-time only:
`PUT /api/config/orchestrator/reasoner.max_turns` returned success,
`GET /api/reasoner/status` reported the new value (the API process builds its
own), and the worker kept using what it booted with. Found while building
`planning-recovery-v1`, whose whole mechanism is changing `reasoner.max_turns`
mid-run — the change was accepted and silently ignored.

Resolving per CALL is what fixes it for a long-lived holder. The cost is a
config read and a key decrypt against an LLM round trip, and the granularity is
right: a planning call is a whole session, so nothing is ever swapped mid-turn.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.infra.reasoner.live_reasoner import LiveReasoner
from agent_orchestrator.infra.reasoner.stub_reasoner import StubReasoner


def _plan() -> Plan:
    return Plan(id="p1", project_id="proj-1", brief="build a slug helper", phase=PlanPhase.DISCOVERY)


class _Counting:
    """Counts how often the composition root was asked to resolve a reasoner."""

    def __init__(self) -> None:
        self.builds = 0

    def __call__(self) -> StubReasoner:
        self.builds += 1
        return StubReasoner()


def test_every_call_resolves_the_reasoner_again() -> None:
    build = _Counting()
    live = LiveReasoner(build)

    asyncio.run(live.converse(_plan(), [], "hello", "discovery"))
    asyncio.run(live.converse(_plan(), [], "again", "discovery"))

    assert build.builds == 2


def test_the_reasoner_is_not_resolved_until_it_is_used() -> None:
    """Construction must stay free: the worker builds its handler at boot, and
    a missing master key must not take the process down before any planning."""
    build = _Counting()

    LiveReasoner(build)

    assert build.builds == 0


def test_a_config_change_between_calls_is_picked_up() -> None:
    """The behaviour the entry is about, without a live provider: swap what the
    factory returns and the next call uses it."""
    swapped: list[str] = []

    class _Recording(StubReasoner):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

        async def architect_cycle(self, plan):  # type: ignore[no-untyped-def]
            swapped.append(self.label)
            return await super().architect_cycle(plan)

    current = ["before"]
    live = LiveReasoner(lambda: _Recording(current[0]))

    asyncio.run(live.architect_cycle(_plan()))
    current[0] = "after"          # the operator writes a new reasoner.* key
    asyncio.run(live.architect_cycle(_plan()))

    assert swapped == ["before", "after"]


def test_a_broken_configuration_surfaces_at_the_call_it_breaks() -> None:
    """An invalid config used to fail at worker boot. Resolving per call moves
    that failure to the planning call, where `_handle_reasoner_failure` records
    it against the plan instead of it being a startup crash nobody reads."""

    def explode() -> StubReasoner:
        raise RuntimeError("REASONER_CONFIG_INVALID: no such model")

    live = LiveReasoner(explode)

    with pytest.raises(RuntimeError, match="REASONER_CONFIG_INVALID"):
        asyncio.run(live.converse(_plan(), [], "hello", "discovery"))


def test_it_forwards_every_transform_on_the_port() -> None:
    """A decorator that silently dropped one of the four would fail closed in a
    way no other test covers — enrichment would simply never run."""
    build = _Counting()
    live = LiveReasoner(build)
    plan = _plan()

    asyncio.run(live.converse(plan, [], "hello", "discovery"))
    asyncio.run(live.architect_cycle(plan))

    assert build.builds == 2
    assert hasattr(live, "enrich_goal")
    assert hasattr(live, "enrich_goal_contract")
