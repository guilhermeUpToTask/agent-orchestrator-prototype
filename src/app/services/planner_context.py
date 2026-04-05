"""
src/app/services/planner_context.py — PlannerContextAssembler.

Assembles the full context object that the planning layer receives before
generating a new roadmap.  It is a read-only application service: it pulls
from four sources and returns a single PlannerContext value object.

Sources:
  1. ProjectSpec          — architectural constraints (forbidden, required,
                            tech stack, directory rules)
  2. ProjectState         — accumulated planner memory (decisions, current
                            architecture, free-form context)
  3. GoalRepository       — current goal execution state (what is planned,
                            running, or already merged)
  4. TaskRepository       — granular task execution state within active goals

The planner agent reads this context and uses it to:
  - Avoid contradicting prior architectural decisions
  - Not re-plan work that is already done or in-flight
  - Understand what the current architecture looks like
  - Respect tech-stack constraints when proposing new goals

Design:
  PlannerContextAssembler is injected with ports, not concrete adapters.
  It never writes — all mutations go through their respective use cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import structlog

from src.domain.aggregates.goal import GoalStatus
from src.domain.ports.project_state import DecisionEntry, ProjectStatePort
from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories import TaskRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort
from src.domain.value_objects.status import TaskStatus

log = structlog.get_logger(__name__)

# Well-known project state keys the planner writes and reads.
# Well-known project state keys written and read by the planner.
# STATE_KEY_DECISIONS is no longer used — decisions are now stored as
# structured DecisionEntry objects via ProjectStatePort.write_decision().
STATE_KEY_CURRENT_ARCH = "current_arch"
STATE_KEY_CONTEXT      = "context"


@dataclass(frozen=True)
class GoalSnapshot:
    """Lightweight summary of a goal's current state for planner consumption."""
    goal_id: str
    name: str
    description: str
    status: str
    feature_tag: Optional[str]
    depends_on: list[str]
    progress: tuple[int, int]   # (merged_tasks, total_tasks)
    branch: str


@dataclass(frozen=True)
class PlanningContextSnapshot:
    """
    Immutable snapshot of everything the planner needs to reason about.

    Passed verbatim to the planning agent as structured context.  The agent
    should never receive raw repository objects — only this sanitised read
    model.

    Fields:
      architecture_constraints  — from ProjectSpec.get_architecture_constraints()
      decisions                 — list of active DecisionEntry objects
      current_arch              — current architecture description, or empty string
      extra_context             — free-form context the planner accumulated, or ""
      goals                     — snapshot of all non-terminal goals
      merged_goal_names         — names of all goals that reached MERGED
      active_task_count         — number of tasks currently ASSIGNED or IN_PROGRESS
      pending_goal_count        — number of goals still in PENDING state
      plan_status               — ProjectPlanStatus.value or None
      current_phase_goal        — Phase.goal of active phase
      planned_phases             — one-sentence goals of future phases
    """
    architecture_constraints: dict[str, Any]
    decisions: list[DecisionEntry]
    current_arch: str
    extra_context: str
    goals: list[GoalSnapshot]
    merged_goal_names: list[str]
    active_task_count: int
    pending_goal_count: int
    plan_status: Optional[str]
    current_phase_goal: Optional[str]
    planned_phases: list[str]

class PlanningContextRenderer:
    """Render planning context snapshots into prompt-friendly text."""

    def render_markdown(self, snapshot: PlanningContextSnapshot) -> str:
        """Render snapshot as structured markdown."""
        sections: list[str] = []

        # 1. Architectural constraints
        c = snapshot.architecture_constraints
        sections.append("## Project constraints")
        sections.append(f"**Project**: {c.get('project', '?')}")
        sections.append(f"**Domain**: {c.get('domain', '?')}")
        ts = c.get("tech_stack", {})
        if ts.get("backend"):
            sections.append(f"**Backend**: {', '.join(ts['backend'])}")
        if ts.get("database"):
            sections.append(f"**Database**: {', '.join(ts['database'])}")
        cst = c.get("constraints", {})
        if cst.get("forbidden"):
            sections.append(f"**Forbidden**: {', '.join(cst['forbidden'])}")
        if cst.get("required"):
            sections.append(f"**Required**: {', '.join(cst['required'])}")

        # 2. Accumulated decisions — grouped by domain
        if snapshot.decisions:
            sections.append("\n## Architectural decisions")
            # Group by domain
            by_domain: dict[str, list[DecisionEntry]] = {}
            for d in snapshot.decisions:
                by_domain.setdefault(d.domain or "general", []).append(d)
            for domain, entries in sorted(by_domain.items()):
                sections.append(f"\n### {domain.capitalize()}")
                for entry in entries:
                    tag = f" (feature: {entry.feature_tag})" if entry.feature_tag else ""
                    sections.append(f"**[{entry.id}]**{tag} — {entry.date}")
                    sections.append(entry.content)
                    # Render spec_changes if present
                    if entry.spec_changes and not entry.spec_changes.is_empty:
                        sections.append("")
                        sections.append("**Spec changes:**")
                        sc = entry.spec_changes
                        if sc.add_required:
                            sections.append(f"  - Add required: {', '.join(sc.add_required)}")
                        if sc.add_forbidden:
                            sections.append(f"  - Add forbidden: {', '.join(sc.add_forbidden)}")
                        if sc.remove_required:
                            sections.append(f"  - Remove required: {', '.join(sc.remove_required)}")
                        if sc.remove_forbidden:
                            sections.append(f"  - Remove forbidden: {', '.join(sc.remove_forbidden)}")

        # 3. Current architecture description
        if snapshot.current_arch:
            sections.append("\n## Current architecture")
            sections.append(snapshot.current_arch)

        # 4. Extra context
        if snapshot.extra_context:
            sections.append("\n## Additional context")
            sections.append(snapshot.extra_context)

        # 5. Project plan state
        if snapshot.plan_status:
            sections.append("\n## Project plan")
            sections.append(f"Status: {snapshot.plan_status}")
            if snapshot.current_phase_goal:
                sections.append(f'Current phase: "{snapshot.current_phase_goal}"')
            if snapshot.planned_phases:
                sections.append("Planned phases:")
                for phase_desc in snapshot.planned_phases:
                    sections.append(f"  - {phase_desc}")

        # 6. Execution state summary
        sections.append("\n## Current execution state")
        sections.append(
            f"- {snapshot.pending_goal_count} goal(s) pending"
            f", {snapshot.active_task_count} task(s) actively running"
        )
        if snapshot.merged_goal_names:
            sections.append(
                f"- Completed goals: {', '.join(snapshot.merged_goal_names)}"
            )
        if snapshot.goals:
            sections.append("\n### In-progress goals")
            for g in snapshot.goals:
                merged, total = g.progress
                sections.append(
                    f"- **{g.name}** [{g.status}] — {merged}/{total} tasks merged"
                    + (f" (feature: {g.feature_tag})" if g.feature_tag else "")
                )

        return "\n".join(sections)


class PlannerContextAssembler:
    """
    Read-only application service: assembles PlannerContext from all sources.

    Call assemble() once per planning session.  The result is a stable
    snapshot — subsequent repository mutations do not affect the returned
    object.
    """

    def __init__(
        self,
        spec: ProjectSpec,
        project_state: ProjectStatePort,
        goal_repo: GoalRepositoryPort,
        task_repo: TaskRepositoryPort,
        plan_repo: ProjectPlanRepositoryPort,  # NEW
    ) -> None:
        self._spec          = spec
        self._project_state = project_state
        self._goal_repo     = goal_repo
        self._task_repo     = task_repo
        self._plan_repo     = plan_repo

    def assemble(self) -> PlanningContextSnapshot:
        """Build and return a PlanningContextSnapshot."""
        log.info("planner_context.assembling")

        constraints = self._read_spec_context()
        decisions, current_arch, extra_ctx = self._read_project_state_context()
        visible_goals, merged_names, pending_count = self._read_goal_context()
        active_task_count = self._read_task_load()
        plan_status, current_phase_goal, planned_phases = self._read_plan_state()

        ctx = PlanningContextSnapshot(
            architecture_constraints=constraints,
            decisions=decisions,
            current_arch=current_arch,
            extra_context=extra_ctx,
            goals=visible_goals,
            merged_goal_names=merged_names,
            active_task_count=active_task_count,
            pending_goal_count=pending_count,
            plan_status=plan_status,
            current_phase_goal=current_phase_goal,
            planned_phases=planned_phases,
        )

        log.info(
            "planner_context.assembled",
            goal_count=len(visible_goals),
            merged_count=len(merged_names),
            active_tasks=active_task_count,
            has_decisions=bool(decisions),
            has_arch=bool(current_arch),
            plan_status=plan_status,
        )
        return ctx

    def _read_spec_context(self) -> dict[str, Any]:
        return self._spec.get_architecture_constraints()

    def _read_project_state_context(self) -> tuple[list[DecisionEntry], str, str]:
        decisions = self._project_state.list_decisions(status="active")
        current_arch = self._project_state.read_state(STATE_KEY_CURRENT_ARCH) or ""
        extra_context = self._project_state.read_state(STATE_KEY_CONTEXT) or ""
        return decisions, current_arch, extra_context

    def _read_goal_context(self) -> tuple[list[GoalSnapshot], list[str], int]:
        all_goals = self._goal_repo.list_all()
        merged_names = [g.name for g in all_goals if g.status == GoalStatus.MERGED]
        visible_goals = [
            GoalSnapshot(
                goal_id=g.goal_id,
                name=g.name,
                description=g.description,
                status=g.status.value,
                feature_tag=g.feature_tag,
                depends_on=list(g.depends_on),
                progress=g.progress(),
                branch=g.branch,
            )
            for g in all_goals
            if not g.is_terminal()
        ]
        pending_count = sum(1 for g in all_goals if g.status == GoalStatus.PENDING)
        return visible_goals, merged_names, pending_count

    def _read_task_load(self) -> int:
        all_tasks = self._task_repo.list_all()
        return sum(1 for t in all_tasks if t.status in TaskStatus.active())

    def _read_plan_state(self) -> tuple[Optional[str], Optional[str], list[str]]:
        plan = self._plan_repo.get()
        if plan is None:
            return None, None, []

        plan_status = plan.status.value
        current_phase = plan.current_phase()
        current_phase_goal = current_phase.goal if current_phase else None
        planned_phases = [f"Phase {phase.index}: {phase.goal}" for phase in plan.planned_phases()]
        return plan_status, current_phase_goal, planned_phases
