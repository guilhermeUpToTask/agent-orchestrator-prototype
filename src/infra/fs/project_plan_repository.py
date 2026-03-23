"""
src/infra/fs/project_plan_repository.py — YAML filesystem adapter for ProjectPlanRepositoryPort.

One YAML file per project: project_plan.yaml
Follows the same atomic-write and quarantine patterns as YamlGoalRepository.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectBrief,
    ProjectPlan,
    ProjectPlanStatus,
    HistoryEntry,
)
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort


class YamlProjectPlanRepository(ProjectPlanRepositoryPort):
    """Stores a single ProjectPlan aggregate as a YAML file."""

    def __init__(self, plan_path: str | Path) -> None:
        self._path = Path(plan_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._quarantine = self._path.parent / "quarantine"
        self._quarantine.mkdir(exist_ok=True)

    def save(self, plan: ProjectPlan) -> None:
        self._atomic_write(self._path, plan)

    def load(self) -> ProjectPlan:
        if not self._path.exists():
            raise KeyError(f"ProjectPlan not found at {self._path}")
        data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return _deserialize_plan(data)

    def exists(self) -> bool:
        return self._path.exists()

    def get(self) -> Optional[ProjectPlan]:
        try:
            return self.load()
        except KeyError:
            return None

    def _atomic_write(self, path: Path, plan: ProjectPlan) -> None:
        tmp = path.with_suffix(".tmp")
        try:
            data = _serialize_plan(plan)
            tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


class InMemoryProjectPlanRepository(ProjectPlanRepositoryPort):
    """Volatile in-memory adapter for tests and dry-run mode."""

    def __init__(self) -> None:
        self._plan: Optional[ProjectPlan] = None

    def save(self, plan: ProjectPlan) -> None:
        self._plan = plan

    def load(self) -> ProjectPlan:
        if self._plan is None:
            raise KeyError("ProjectPlan not found (in-memory)")
        return self._plan

    def exists(self) -> bool:
        return self._plan is not None

    def get(self) -> Optional[ProjectPlan]:
        return self._plan


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_plan(plan: ProjectPlan) -> dict:
    """Serialize ProjectPlan to dict for YAML."""
    return {
        "plan_id": plan.plan_id,
        "status": plan.status.value,
        "vision": plan.vision,
        "brief": _serialize_brief(plan.brief) if plan.brief else None,
        "phases": [_serialize_phase(p) for p in plan.phases],
        "current_phase_index": plan.current_phase_index,
        "architecture_summary": plan.architecture_summary,
        "state_version": plan.state_version,
        "history": [
            {
                "event": h.event,
                "actor": h.actor,
                "detail": h.detail,
                "timestamp": h.timestamp.isoformat(),
            }
            for h in plan.history
        ],
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }


def _serialize_brief(brief: ProjectBrief) -> dict:
    return {
        "vision": brief.vision,
        "constraints": brief.constraints,
        "phase_1_exit_criteria": brief.phase_1_exit_criteria,
        "open_questions": brief.open_questions,
    }


def _serialize_phase(phase: Phase) -> dict:
    return {
        "index": phase.index,
        "name": phase.name,
        "goal": phase.goal,
        "goal_names": phase.goal_names,
        "status": phase.status.value,
        "lessons": phase.lessons,
        "exit_criteria": phase.exit_criteria,
    }


def _deserialize_plan(data: dict) -> ProjectPlan:
    """Deserialize dict to ProjectPlan."""
    from datetime import datetime, timezone

    # Parse phases
    phases = []
    for p_data in data.get("phases", []):
        phases.append(Phase(
            index=p_data["index"],
            name=p_data["name"],
            goal=p_data["goal"],
            goal_names=p_data.get("goal_names", []),
            status=PhaseStatus(p_data["status"]),
            lessons=p_data.get("lessons", ""),
            exit_criteria=p_data.get("exit_criteria", ""),
        ))

    # Parse brief
    brief_data = data.get("brief")
    brief = None
    if brief_data:
        brief = ProjectBrief(
            vision=brief_data["vision"],
            constraints=brief_data.get("constraints", []),
            phase_1_exit_criteria=brief_data.get("phase_1_exit_criteria", ""),
            open_questions=brief_data.get("open_questions", []),
        )

    # Parse history
    history_data = data.get("history", [])
    history = []
    for h in history_data:
        # Use HistoryEntry.model_validate to create proper Pydantic instances
        history.append(HistoryEntry(
            event=h["event"],
            actor=h["actor"],
            detail=h.get("detail", {}),
            timestamp=datetime.fromisoformat(h["timestamp"].replace("Z", "+00:00")),
        ))

    return ProjectPlan(
        plan_id=data["plan_id"],
        status=ProjectPlanStatus(data["status"]),
        vision=data.get("vision", ""),
        brief=brief,
        phases=phases,
        current_phase_index=data.get("current_phase_index", -1),
        architecture_summary=data.get("architecture_summary", ""),
        state_version=data.get("state_version", 1),
        history=history,
        created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
    )
