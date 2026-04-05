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
            "2. Use `propose_decision` to propose decisions (can be called multiple times)\n"
            "3. Use `propose_phase_plan` to propose the initial phase(s)\n"
            "4. Use `submit_architecture` when ready for approval"
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
