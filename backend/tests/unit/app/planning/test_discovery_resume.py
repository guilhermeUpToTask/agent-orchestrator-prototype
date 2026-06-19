"""
Unit tests for StartDiscoveryUseCase resilience:
  - a resumable session replays its saved transcript into the runtime, and
  - a transient runtime failure keeps the session RUNNING (resumable) while a
    permanent one fails it terminally.
"""
from unittest.mock import MagicMock

from src.app.planning.sessions.usecases import StartDiscoveryUseCase
from src.domain.aggregates.planner_session import (
    PlannerMode,
    PlannerSession,
    PlannerSessionStatus,
)
from src.domain.ports.planner import PlannerOutput, PlannerRuntimeError, PlannerRuntimePort


class _CapturingRuntime(PlannerRuntimePort):
    """Captures prior_turns; raises a configured error (or returns output)."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.prior_turns: object = "unset"

    def run_session(
        self,
        prompt,
        tools,
        max_turns=15,
        session_callback=None,
        require_submit=True,
        cancel_check=None,
        prior_turns=None,
    ) -> PlannerOutput:
        self.prior_turns = prior_turns
        if self._error is not None:
            raise self._error
        return PlannerOutput(reasoning="", roadmap_raw={}, raw_text="")


def _running_session_with_turns() -> PlannerSession:
    s = PlannerSession.create("vision", mode=PlannerMode.DISCOVERY)
    s.start()
    s.add_turn("assistant", [{"text": "Q?", "tool_calls": []}], 0)
    s.add_turn("tool_result", [{"tool_use_id": "c1", "name": "ask_question", "content": "A"}], 1)
    return s


def _make_uc(runtime, session):
    plan_repo = MagicMock()
    plan_repo.get.return_value = None  # no existing plan → discovery may start
    session_repo = MagicMock()
    support = MagicMock()
    support.find_resumable_session.return_value = session
    support.build_discovery_tools.return_value = []
    support.build_discovery_prompt.return_value = "prompt"
    support.make_session_callback.return_value = lambda *a, **k: None
    uc = StartDiscoveryUseCase(
        plan_repo=plan_repo,
        session_repo=session_repo,
        runtime=runtime,
        support=support,
    )
    return uc


def test_resume_replays_saved_transcript():
    session = _running_session_with_turns()
    runtime = _CapturingRuntime(error=PlannerRuntimeError("blip", transient=True))
    uc = _make_uc(runtime, session)

    uc.execute()

    assert runtime.prior_turns == [
        {"role": "assistant", "content": [{"text": "Q?", "tool_calls": []}]},
        {"role": "tool_result", "content": [{"tool_use_id": "c1", "name": "ask_question", "content": "A"}]},
    ]


def test_transient_failure_keeps_session_resumable():
    session = _running_session_with_turns()
    runtime = _CapturingRuntime(error=PlannerRuntimeError("timeout", transient=True))
    uc = _make_uc(runtime, session)

    result = uc.execute()

    assert result.resumable is True
    assert result.needs_approval is False
    assert session.status == PlannerSessionStatus.RUNNING  # still resumable


def test_permanent_failure_fails_terminally():
    session = _running_session_with_turns()
    runtime = _CapturingRuntime(error=PlannerRuntimeError("no tool use", transient=False))
    uc = _make_uc(runtime, session)

    result = uc.execute()

    assert result.resumable is False
    assert session.status == PlannerSessionStatus.FAILED


def test_fresh_session_passes_no_prior_turns():
    runtime = _CapturingRuntime(error=PlannerRuntimeError("blip", transient=True))
    uc = _make_uc(runtime, session=None)  # no resumable session → fresh one created

    uc.execute()

    assert runtime.prior_turns is None
