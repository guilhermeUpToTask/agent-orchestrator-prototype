"""
src/infra/db/unit_of_work.py — SqliteUnitOfWork (the UnitOfWork port).

RE-ENTERABLE by design: one drive_plan pass enters the same UoW object many
times sequentially (read txn in the dispatcher, txn1, finalize txn, ...), so
__enter__ opens a FRESH Session + transaction every time and __exit__ commits
or rolls back and closes it. Holding one session across with-blocks would make
the outbox-rollback tests pass for the wrong reason.

One UoW instance per worker/request — NOT thread-safe (the bound session is
shared mutable state); give each thread its own instance.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import Clock
from src.infra.db.outbox import SqliteOutbox
from src.infra.db.plan_repository import SqlitePlanRepository


class SqliteUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.plans = SqlitePlanRepository(session_factory, clock)
        self.outbox = SqliteOutbox()

    def __enter__(self) -> "SqliteUnitOfWork":
        if self._session is not None:
            raise RuntimeError("SqliteUnitOfWork transactions cannot be nested")
        self._session = self._session_factory()
        self.plans.bind(self._session)
        self.outbox.bind(self._session)
        return self

    def __exit__(self, *exc: object) -> None:
        session = self._session
        assert session is not None
        try:
            if exc[0] is None:
                session.commit()  # state + outbox commit together
            else:
                session.rollback()  # rollback discards staged events with the state
        finally:
            session.close()
            self.plans.unbind()
            self.outbox.unbind()
            self._session = None
