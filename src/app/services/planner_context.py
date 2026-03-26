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
class PlannerContext:
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

    def to_prompt_context(self) -> str:
        """
        Render the context as a structured markdown string suitable for
        inclusion in a planner prompt.
        """
        sections: list[str] = []

        # 1. Architectural constraints
        c = self.architecture_constraints
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
        if self.decisions:
            sections.append("\n## Architectural decisions")
            # Group by domain
            by_domain: dict[str, list[DecisionEntry]] = {}
            for d in self.decisions:
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
        if self.current_arch:
            sections.append("\n## Current architecture")
            sections.append(self.current_arch)

        # 4. Extra context
        if self.extra_context:
            sections.append("\n## Additional context")
            sections.append(self.extra_context)

        # 5. Project plan state
        if self.plan_status:
            sections.append("\n## Project plan")
            sections.append(f"Status: {self.plan_status}")
            if self.current_phase_goal:
                sections.append(f'Current phase: "{self.current_phase_goal}"')
            if self.planned_phases:
                sections.append("Planned phases:")
                for phase_desc in self.planned_phases:
                    sections.append(f"  - {phase_desc}")

        # 6. Execution state summary
        sections.append("\n## Current execution state")
        sections.append(
            f"- {self.pending_goal_count} goal(s) pending"
            f", {self.active_task_count} task(s) actively running"
        )
        if self.merged_goal_names:
            sections.append(
                f"- Completed goals: {', '.join(self.merged_goal_names)}"
            )
        if self.goals:
            sections.append("\n### In-progress goals")
            for g in self.goals:
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

    def assemble(self) -> PlannerContext:
        """Build and return a PlannerContext snapshot."""
        log.info("planner_context.assembling")

        # 1. Architectural constraints from ProjectSpec
        constraints = self._spec.get_architecture_constraints()

        # 2. Persistent planner memory
        decisions    = self._project_state.list_decisions(status="active")
        current_arch = self._project_state.read_state(STATE_KEY_CURRENT_ARCH) or ""
        extra_ctx    = self._project_state.read_state(STATE_KEY_CONTEXT) or ""

        # 3. Goal execution state
        all_goals = self._goal_repo.list_all()
        merged_names = [g.name for g in all_goals if g.status == GoalStatus.MERGED]

        # Non-terminal goals are surfaced to the planner so it avoids
        # re-proposing work that is already planned or in-flight.
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

        # 4. Active task count (ASSIGNED + IN_PROGRESS) — gives the planner a
        #    sense of current load without exposing full task details.
        all_tasks = self._task_repo.list_all()
        active_task_count = sum(
            1 for t in all_tasks if t.status in TaskStatus.active()
        )

        # 5. Project plan state
        plan = self._plan_repo.get()
        plan_status = None
        current_phase_goal = None
        planned_phases = []

        if plan is not None:
            plan_status = plan.status.value
            current_phase = plan.current_phase()
            if current_phase:
                current_phase_goal = current_phase.goal
            # Get planned phases (future phases with PLANNED status)
            for phase in plan.planned_phases():
                planned_phases.append(f"Phase {phase.index}: {phase.goal}")

        ctx = PlannerContext(
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
