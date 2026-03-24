"""
src/app/usecases/propose_spec_change.py — ProposeSpecChange use case.

Responsibility:
  Accept a change proposal for the ProjectSpec, validate that the resulting
  spec would still be coherent, and persist a *pending proposal* to disk for
  operator review.

Key invariant:
  This use case NEVER writes the new spec directly to project_spec.yaml.
  It writes only to project_spec.proposed.yaml, leaving the live spec intact
  until an operator explicitly approves the change via 'orchestrate spec apply'.

Proposal lifecycle:
  1. Caller submits a ChangeProposal.
  2. ProposeSpecChange validates the proposed new state against invariants.
  3. The proposed spec is written atomically to:
       <project_home>/project_spec.proposed.yaml
  4. An operator runs 'orchestrate spec apply' to promote the proposal.
  5. The apply command calls repo.save() and removes the .proposed file.

Design note on path ownership:
  The proposal path is derived from the ProjectSpecRepository, not duplicated
  here. The repository owns the path convention; this use case only decides
  *what* to write, not *where*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

from src.domain.project_spec import (
    ProjectSpec,
    ProjectSpecRepository,
    SpecNotFoundError,
)

log = structlog.get_logger(__name__)

_PROPOSED_FILENAME = "project_spec.proposed.yaml"


@dataclass
class ChangeProposal:
    """
    Declarative description of requested changes to a ProjectSpec.

    All fields are optional. Only set fields are applied; everything else is
    carried forward from the current live spec unchanged.
    """

    new_version: str | None = None
    new_objective_desc: str | None = None
    new_objective_domain: str | None = None
    add_forbidden: list[str] = field(default_factory=list)
    remove_forbidden: list[str] = field(default_factory=list)
    add_required: list[str] = field(default_factory=list)
    remove_required: list[str] = field(default_factory=list)
    add_directory: dict[str, str] | None = None
    remove_directory: str | None = None
    rationale: str = ""


@dataclass(frozen=True)
class ProposalResult:
    """Outcome of a propose() call."""

    accepted: bool
    proposed_spec: ProjectSpec | None
    proposal_path: str | None
    rejection_reason: str | None = None

    def __str__(self) -> str:
        if self.accepted:
            return f"ProposalResult: ACCEPTED → {self.proposal_path}"
        return f"ProposalResult: REJECTED — {self.rejection_reason}"


class ProposeSpecChange:
    """
    Use case: propose a change to the ProjectSpec.

    Writes a project_spec.proposed.yaml for operator review.
    Never touches the live project_spec.yaml.

    The proposal path is derived from the same directory the repository
    uses for the live spec — no path duplication.
    """

    def __init__(self, spec_repo: ProjectSpecRepository) -> None:
        self._repo = spec_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self, project_name: str, proposal: ChangeProposal
    ) -> ProposalResult:
        """
        Validate and stage a change proposal for operator review.

        Returns ProposalResult with .accepted=True when the proposed spec
        passes all invariants. On failure, .accepted=False and
        .rejection_reason explains why.
        """
        log.info(
            "propose_spec_change.received",
            project=project_name,
            rationale=proposal.rationale[:120] if proposal.rationale else "",
        )

        # 1. Load current spec
        try:
            current_spec = self._repo.load(project_name)
        except SpecNotFoundError as exc:
            return ProposalResult(
                accepted=False,
                proposed_spec=None,
                proposal_path=None,
                rejection_reason=str(exc),
            )

        # 2. Apply changes to produce a candidate spec
        try:
            candidate = current_spec._apply_approved_change(
                new_version=proposal.new_version,
                new_objective_description=proposal.new_objective_desc,
                new_objective_domain=proposal.new_objective_domain,
                add_forbidden=proposal.add_forbidden,
                remove_forbidden=proposal.remove_forbidden,
                add_required=proposal.add_required,
                remove_required=proposal.remove_required,
                add_directory=proposal.add_directory,
                remove_directory=proposal.remove_directory,
            )
        except (ValueError, TypeError) as exc:
            return ProposalResult(
                accepted=False,
                proposed_spec=None,
                proposal_path=None,
                rejection_reason=f"Invalid change proposal: {exc}",
            )

        # 3. Self-consistency check on the candidate
        violations = candidate.validate_structure()
        if violations:
            return ProposalResult(
                accepted=False,
                proposed_spec=None,
                proposal_path=None,
                rejection_reason=(
                    "Proposed spec has structural violations: "
                    + "; ".join(violations)
                ),
            )

        # 4. Write to .proposed.yaml — path derived from the repo
        proposal_path = self._write_proposal(project_name, candidate, proposal)

        log.info(
            "propose_spec_change.staged",
            project=project_name,
            proposal_path=str(proposal_path),
            proposed_version=candidate.meta.version,
        )

        return ProposalResult(
            accepted=True,
            proposed_spec=candidate,
            proposal_path=str(proposal_path),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _proposal_path(self, project_name: str) -> Path:
        """
        Derive the proposal file path from the repository's path convention.

        The repository owns path knowledge; we ask it for the spec path and
        replace the filename — no independent path logic here.
        """
        try:
            return self._repo.proposal_path(project_name)
        except NotImplementedError:
            return Path.cwd() / _PROPOSED_FILENAME

    def _write_proposal(
        self,
        project_name: str,
        candidate: ProjectSpec,
        proposal: ChangeProposal,
    ) -> Path:
        """Write the proposed spec + rationale comment to .proposed.yaml."""
        data: dict[str, Any] = candidate.to_dict()

        if proposal.rationale:
            data["_proposal_rationale"] = proposal.rationale

        content = (
            "# PENDING PROPOSAL — NOT ACTIVE\n"
            "# Review with: orchestrate spec diff\n"
            "# Apply with:  orchestrate spec apply\n"
            "#\n"
        )
        content += yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
            indent=2,
        )

        try:
            return self._repo.save_proposal(project_name, content)
        except NotImplementedError:
            path = self._proposal_path(project_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return path
