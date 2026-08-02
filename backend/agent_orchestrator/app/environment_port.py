"""The ProjectEnvironment port: bring a cycle's assembled tree UP, and check
that the thing actually works.

The gap this closes. Verification modes are `tdd | characterization |
executable_check`, so the system can prove *a command exited 0 against this
commit* and cannot prove *the application works*. For a library, a CLI, a parser
or a rules engine that distinction barely matters — the tests are the product's
contract. For the web application most people imagine when they hear "builds
software", it is the whole question, and today nobody can tell from the evidence
document.

Two ports, two jobs, two words — do NOT merge them:
  * `Sandbox` (agent_orchestrator/app/sandbox_port.py) confines ONE task-attempt
    subprocess.
  * `ProjectEnvironment` containerizes a WHOLE project so a scenario can be run
    against it.

Deliberately NOT a domain concept. The frozen domain never sees these types; it
already has a `cycle_verification` activity label
(`planner_orchestrator.py:932`) naming a slot with no behaviour behind it, and
this fills that slot from the application layer.

**The verdict is advisory and NEVER blocks publication.** A flaky acceptance run
that refuses to publish costs more trust than it earns, and `start_replan`
already exists as the "fix it instead" path — so no new `OutputDisposition`
value is needed and none is added.

**The operator authors how to boot it.** Image, command, port, healthcheck: an
LLM-authored boot shell run against a live application is the failure mode this
design avoids. The reasoner may later propose *what to check*, from the cycle's
own approved intent; it never proposes how to start the thing.

`NoEnvironment` (agent_orchestrator/infra/environment/no_environment.py) is
today's behaviour and the PERMANENT fallback, not a placeholder — the
`NoSandbox` and `NoForge` principle. A project with no environment configured
records a `skipped` verdict and publishes exactly as it does now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# `skipped` is a first-class outcome, not a failure: no environment configured
# is the default and supported state, and it must read differently from "we
# tried to boot it and could not".
AcceptanceOutcome = Literal["passed", "failed", "errored", "skipped"]

# Where the run was triggered. Both use the same machinery; they differ only in
# how much of the cycle exists yet, which is worth recording beside the verdict.
AcceptanceTrigger = Literal["goal_merge", "pre_publication"]


@dataclass(frozen=True)
class EnvironmentSpec:
    """How to boot ONE project. Authored by the operator, never by a model.

    Deliberately minimal for this slice: add fields as a real adapter needs
    them, never widen `verify()`'s signature.
    """

    image: str
    command: str | None = None
    port: int | None = None
    healthcheck: str | None = None
    # Shell commands run against the booted application. The operator's, not a
    # model's — see the module docstring.
    scenario: list[str] = field(default_factory=list)
    startup_timeout_seconds: int = 120


@dataclass(frozen=True)
class AcceptanceVerdict:
    """What one acceptance run concluded. Advisory: nothing gates on it."""

    outcome: AcceptanceOutcome
    summary: str
    detail: str = ""
    duration_seconds: float = 0.0

    @property
    def is_signal(self) -> bool:
        """Whether this verdict says anything at all about the application.

        `skipped` does not — it reports that nobody asked. Read models use this
        so an unconfigured project shows nothing rather than a reassuring green.
        """
        return self.outcome != "skipped"


@runtime_checkable
class ProjectEnvironment(Protocol):
    def verify(self, repo: Path, ref: str, spec: EnvironmentSpec | None) -> AcceptanceVerdict:
        """Boot `ref` out of `repo`, run the scenario, tear down, and report.

        MUST NOT raise. An adapter that cannot boot the tree returns
        `errored` — an acceptance run is advisory, and a crash in it must never
        take down the promotion or the publication gate it was observing.
        """
        ...
