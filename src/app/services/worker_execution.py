"""
src/app/services/worker_execution.py — Backward-compatibility re-export.

TaskExecuteUseCase has moved to:
    src/app/usecases/task_execute.py

This stub re-exports both the class under its old name and LeaseRefresher
so existing test patches on this module path continue to work.
"""
from src.app.usecases.task_execute import TaskExecuteUseCase as WorkerExecutionService  # noqa: F401
from src.infra.redis_adapters.lease_refresher import LeaseRefresher  # noqa: F401

__all__ = ["WorkerExecutionService", "LeaseRefresher"]
