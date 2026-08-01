"""Is a failed goal→cycle merge worth retrying, or is it a decision?

`_promote_goal` used to open a `goal_promotion_failure` block on the FIRST
exception from `merge_goal`, with no retry, advertising only `start_replan`. A
git worktree still being cleaned up, or an index lock held for a moment, would
therefore throw away a fully verified goal and ask a person to regenerate the
whole cycle.

A real merge CONFLICT is different in kind: two goals changed the same lines, and
no number of retries resolves it. Everything the classification gets wrong should
err toward BLOCKING — a spurious block is annoying and still shows the operator
the message, whereas a retry loop on an unrecognised error burns a worker
forever. So this is an allowlist of failures known to be environmental, and
anything unfamiliar is permanent.
"""

from __future__ import annotations

# Environmental: the repository was momentarily unusable, not wrong. Each of
# these clears on its own — a stale worktree registration is pruned, a lock is
# released, a full disk is noticed.
_TRANSIENT_MARKERS = (
    "is already checked out",
    "index.lock",
    "unable to create",
    "no space left on device",
    "input/output error",
    "resource temporarily unavailable",
    "could not read username",
    "connection reset",
    "unable to write file",
)

# Genuine disagreements about content, or a missing input. Checked FIRST: a
# conflict message can also mention a path that looks environmental.
_PERMANENT_MARKERS = (
    "conflict",
    "automatic merge failed",
    "would be overwritten",
    "branch is missing",
    "not something we can merge",
    "refusing to merge unrelated histories",
)


def is_transient_merge_failure(message: str) -> bool:
    """True only for failures a later attempt could plausibly clear."""
    text = (message or "").lower()
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return False
    return any(marker in text for marker in _TRANSIENT_MARKERS)


__all__ = ["is_transient_merge_failure"]
