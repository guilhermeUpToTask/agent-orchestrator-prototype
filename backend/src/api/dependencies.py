"""
src/api/dependencies.py — the API's dependency surface over AppContainer.

One process-wide container (set explicitly in tests via set_container, built
from the environment otherwise). Endpoints that mutate plan state take a FRESH
UnitOfWork per request — sync routers run in the threadpool and the UoW is not
thread-safe, so sharing one across requests would interleave transactions.
"""
from __future__ import annotations

import threading

from src.infra.container import AppContainer
from src.infra.db.unit_of_work import SqliteUnitOfWork

_lock = threading.Lock()
_container: AppContainer | None = None


def set_container(container: AppContainer) -> None:
    global _container
    with _lock:
        _container = container


def get_container() -> AppContainer:
    global _container
    with _lock:
        if _container is None:
            _container = AppContainer.from_env()
        return _container


def get_uow() -> SqliteUnitOfWork:
    return get_container().new_unit_of_work()
