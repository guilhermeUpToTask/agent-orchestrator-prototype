"""The truth-test parametrization: every env-based orchestration test runs
against the in-memory fakes AND (as -m integration) the real SQLite
UnitOfWork/repository/outbox. Same tests, same FakeClock/dummy runner — only
the persistence boundary changes."""

from __future__ import annotations

import itertools

import pytest

from tests.support import Env, make_memory_env, make_sqlite_env

_counter = itertools.count()


@pytest.fixture(params=["memory", pytest.param("sqlite", marks=pytest.mark.integration)])
def env_factory(request, tmp_path):
    def build(script=None, agents=None, default_agent_id="a1") -> Env:
        if request.param == "memory":
            return make_memory_env(script, agents, default_agent_id)
        # a fresh db file per build — some tests build several envs
        db_path = tmp_path / f"orchestrator-{next(_counter)}.db"
        return make_sqlite_env(db_path, script, agents, default_agent_id)

    return build
