from pydantic import BaseModel


class RetryPolicy(BaseModel):
    """Domain rule for retry/terminal decisions.

    Owns the *decision* (should we retry? how long to back off?); the adapter
    owns the *mechanism* (actually sleeping, re-invoking). Persisted per-plan so
    it is configurable and survives crashes. This is also what makes
    "retries exhausted -> terminal FAILED" a domain rule rather than adapter magic.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 60.0
    # Exact-match against free-form reasons is brittle; see ../../DESIGN_NOTES.md
    # (typed FailureKind for retry classification).
    non_retryable_reasons: list[str] = ["invalid_input"]

    def should_retry(self, attempts: int, failure_reason: str | None) -> bool:
        if failure_reason is not None and failure_reason in self.non_retryable_reasons:
            return False
        return attempts < self.max_attempts

    def backoff_for(self, attempt: int) -> float:
        """Backoff to wait BEFORE the given attempt (1-based attempt number).

        attempt 1 is the first try -> no backoff (0.0).
        attempt 2 is the first RETRY -> initial_backoff_seconds.
        attempt 3 -> initial * multiplier, etc. (exponent = retry_index - 1).
        Capped at max_backoff_seconds.
        """
        if attempt <= 1:
            return 0.0
        retry_index = attempt - 1  # attempt 2 -> retry 1, attempt 3 -> retry 2
        # exponent retry_index-1 so the FIRST retry pays the base delay (multiplier**0):
        # initial=2, mult=2 -> attempt2=2s, attempt3=4s, attempt4=8s.
        delay = self.initial_backoff_seconds * (
            self.backoff_multiplier ** (retry_index - 1)
        )
        return min(delay, self.max_backoff_seconds)
