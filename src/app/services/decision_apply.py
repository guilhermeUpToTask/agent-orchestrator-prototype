"""
src/app/services/decision_apply.py — Decision atomicity helper.

Apply spec_changes from a DecisionEntry to the live ProjectSpec atomically.

This is the only path — other than the operator escape hatch — by which
the spec is modified. Calling this and write_decision() in sequence
(not in a transaction) is acceptable because:

  1. The decision is written first — if the spec update fails, the decision
     is still recorded and can be re-applied on restart.

  2. On restart, PlannerOrchestrator can detect spec_changes on active
     decisions that are not reflected in the spec and re-apply them.
"""
from __future__ import annotations

import structlog

from src.domain.ports.project_state import DecisionEntry, apply_to_spec
from src.domain.project_spec import ProjectSpecRepository

log = structlog.get_logger(__name__)


def apply_decision_to_spec(
    decision: DecisionEntry,
    spec_repo: ProjectSpecRepository,
    project_name: str,
) -> bool:
    """
    Apply spec_changes from a DecisionEntry to the live ProjectSpec atomically.

    Loads current spec, applies changes via apply_to_spec(), saves back.
    Returns True if spec was changed, False if spec_changes was empty.
    Raises ValueError if spec is not found.

    This is the only path — other than the operator escape hatch — by which
    the spec is modified.
    """
    if decision.spec_changes is None:
        log.debug(
            "decision_apply.no_changes",
            decision_id=decision.id,
            reason="spec_changes is None",
        )
        return False

    if decision.spec_changes.is_empty:
        log.debug(
            "decision_apply.no_changes",
            decision_id=decision.id,
            reason="spec_changes is empty",
        )
        return False

    # Load current spec
    spec = spec_repo.load(project_name)
    if spec is None:
        raise ValueError(f"Project spec not found for project: {project_name}")

    # Apply changes
    try:
        new_spec = apply_to_spec(spec, decision)
    except ValueError as exc:
        log.error(
            "decision_apply.apply_failed",
            decision_id=decision.id,
            error=str(exc),
        )
        raise

    # Save updated spec
    spec_repo.save(project_name, new_spec)

    log.info(
        "decision_apply.applied",
        decision_id=decision.id,
        spec_version=new_spec.meta.version,
    )

    return True
