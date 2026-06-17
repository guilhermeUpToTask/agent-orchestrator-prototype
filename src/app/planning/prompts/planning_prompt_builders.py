from __future__ import annotations

from dataclasses import dataclass

from src.app.services.planner_context import PlanningContextRenderer, PlanningContextSnapshot
from src.domain.aggregates.project_plan import ProjectPlan


@dataclass(frozen=True)
class DiscoveryPromptBuilder:
    renderer: PlanningContextRenderer

    def build(self, ctx: PlanningContextSnapshot) -> str:
        return (
            f"{self.renderer.render_markdown(ctx)}\n\n"
            "---\n\n"
            "## Discovery Request\n\n"
            "Gather project requirements through interactive questions and submit a brief.\n\n"
            "1. Use `ask_question` to ask clarifying questions\n"
            "2. Use `submit_project_brief` when you have enough information"
        )


@dataclass(frozen=True)
class ArchitecturePromptBuilder:
    renderer: PlanningContextRenderer

    def build(self, ctx: PlanningContextSnapshot) -> str:
        return (
            f"{self.renderer.render_markdown(ctx)}\n\n"
            "---\n\n"
            "## Architecture Planning Request\n\n"
            "Propose architectural decisions and a phase plan for the project.\n\n"
            "1. Use `read_project_brief` to see the project brief\n"
            "2. Use `propose_decision` to propose decisions (call once per decision; "
            "do not re-propose an identical decision)\n"
            "3. Use `propose_phase_plan` to propose the phase(s) in a single call "
            "(do not re-propose the same phases). Give every goal its own "
            "description via each phase's optional `goal_descriptions` map so it is "
            "not dispatched with only the phase-level summary.\n"
            "4. Call `submit_architecture` exactly once when ready for approval. If it "
            "returns `{accepted: false, errors: [...]}`, fix exactly those problems "
            "(e.g. a goal missing a description, non-contiguous phase indices) and "
            "resubmit — do not re-propose unchanged items.\n\n"
            "You have a limited number of tool-turns. Work efficiently: propose each "
            "decision and the phase plan once, then submit. If you run out of turns, "
            "whatever you have already proposed is sent to the human approval gate, so "
            "a coherent partial roadmap (at least one decision and one phase) is "
            "acceptable — but submitting explicitly is always preferred."
        )


@dataclass(frozen=True)
class PhaseReviewPromptBuilder:
    renderer: PlanningContextRenderer

    def build(self, ctx: PlanningContextSnapshot, plan: ProjectPlan) -> str:
        return (
            f"{self.renderer.render_markdown(ctx)}\n\n"
            "---\n\n"
            "## Phase Review Request\n\n"
            f"Review the completed phase (index {plan.current_phase_index}) and plan the next phase.\n\n"
            "1. Use `read_phase_summary` to see what was built\n"
            "2. Use `propose_decision` for any new architectural decisions\n"
            "3. Use `propose_next_phase` to define the next phase\n"
            "4. Use `submit_review` with lessons learned and architecture updates"
        )
