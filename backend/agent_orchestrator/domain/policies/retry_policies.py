from __future__ import annotations

from pydantic import BaseModel, Field

from agent_orchestrator.domain.value_objects.lifecycle import FailureKind


class RetryPolicy(BaseModel):
    """Domain rule for retry/terminal decisions.

    Owns the *decision* (should we retry? how long to back off?); the adapter
    owns the *mechanism* (actually sleeping, re-invoking). Persisted per-plan so
    it is configurable and survives crashes. This is also what makes
    "retries exhausted -> terminal FAILED" a domain rule rather than adapter magic.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 900.0
    jitter_ratio: float = 0.2
    kind_max_attempts: dict[FailureKind, int] = Field(
        default_factory=lambda: {
            FailureKind.RATE_LIMIT: 6,
            FailureKind.CONNECTION_ERROR: 5,
        }
    )
    kind_backoff_scale: dict[FailureKind, float] = Field(
        default_factory=lambda: {FailureKind.RATE_LIMIT: 4.0}
    )
    # Domain un-freeze #17. A ceiling is the opposite instrument to
    # `kind_max_attempts`: that one is a FLOOR that grants a transient kind extra
    # tries and cannot be cut by a lower global budget; this one caps a kind
    # whose repetition is EVIDENCE rather than bad luck, and must therefore
    # survive a higher global budget too.
    #
    # A rejected candidate earns exactly one more attempt. Agent output is a
    # sample, so re-running it against the same frozen tests -- now told what was
    # rejected -- is the cheapest recovery available. A third identical rejection
    # says the CONTRACT is wrong, and no number of retries repairs a contract.
    kind_attempt_ceiling: dict[FailureKind, int] = Field(
        default_factory=lambda: {FailureKind.VERIFICATION_ERROR: 2}
    )
    # Typed classification (shared failure taxonomy): a token-limit or auth failure
    # will fail identically on every retry, so it is terminal immediately.
    # VERIFICATION_ERROR was here until un-freeze #17: it made ONE bad agent
    # output block the goal, and the next attempt was never told what went wrong
    # anyway. The prompt feedback (src/app/agent_feedback.py) is what makes the
    # retry mean something; a Class C orchestration race stays terminal through
    # `RuntimeFailure.retryable=False`, which vetoes independently of this set.
    non_retryable_kinds: frozenset[FailureKind] = frozenset(
        {
            FailureKind.TOKEN_LIMIT,
            FailureKind.AUTH_ERROR,
        }
    )

    def should_retry(self, attempts: int, kind: FailureKind | None) -> bool:
        if kind is not None and kind in self.non_retryable_kinds:
            return False
        # A per-kind budget GRANTS a transient kind extra attempts; it is never a
        # ceiling. `max_attempts` is operator-configured (execution.retry_max_attempts)
        # precisely so a provider capacity outage can be ridden out on automatic
        # backoff instead of opening a human-gated block -- taking the kind entry
        # verbatim would let the hardcoded default (6) silently cut a configured
        # baseline of, say, 10 back down for the exact kind that key targets.
        budget = (
            max(self.kind_max_attempts.get(kind, self.max_attempts), self.max_attempts)
            if kind is not None
            else self.max_attempts
        )
        if kind is not None and kind in self.kind_attempt_ceiling:
            budget = min(budget, self.kind_attempt_ceiling[kind])
        return attempts < budget

    def backoff_for(
        self,
        attempt: int,
        *,
        jitter_unit: float = 0.5,
        kind: FailureKind | None = None,
    ) -> float:
        """Backoff to wait BEFORE the given attempt (1-based attempt number).

        attempt 1 is the first try -> no backoff (0.0).
        attempt 2 is the first RETRY -> initial_backoff_seconds.
        attempt 3 -> initial * multiplier, etc. (exponent = retry_index - 1).
        Capped at max_backoff_seconds.
        """
        if attempt <= 1:
            return 0.0
        retry_index = attempt - 1  # attempt 2 -> retry 1, attempt 3 -> retry 2
        scale = self.kind_backoff_scale.get(kind, 1.0) if kind is not None else 1.0
        # exponent retry_index-1 so the FIRST retry pays the base delay (multiplier**0):
        # initial=2, mult=2 -> attempt2=2s, attempt3=4s, attempt4=8s.
        delay = (
            self.initial_backoff_seconds
            * scale
            * (self.backoff_multiplier ** (retry_index - 1))
        )
        bounded_unit = max(0.0, min(1.0, jitter_unit))
        jitter = 1.0 + self.jitter_ratio * ((bounded_unit * 2.0) - 1.0)
        return min(delay * jitter, self.max_backoff_seconds * scale)
