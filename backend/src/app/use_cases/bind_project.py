"""Recovery command for project-less plans quarantined by migration 0009."""

from __future__ import annotations

from src.app.ports import Clock, UnitOfWork
from src.domain.events.outbox import BlockResolved
from src.domain.repositories.project_repo import ProjectRepository


def bind_legacy_project(
    plan_id: str,
    project_id: str,
    uow: UnitOfWork,
    projects: ProjectRepository,
    clock: Clock,
) -> None:
    projects.get(project_id)
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.project_id == project_id:
            return
        block = plan.block
        plan.bind_legacy_project(project_id, clock.now())
        assert block is not None
        plan.bump_version()
        uow.outbox.add(
            BlockResolved(
                plan_id=plan.id,
                block_id=block.id,
                resolution="bind_project",
            )
        )
        uow.plans.save(plan)
