from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from rma_portal.infrastructure.db.repositories import (
    SqlAlchemyDossierNoteRepository,
    SqlAlchemyDossierRepository,
    SqlAlchemyDossierWorkRepository,
    SqlAlchemyNotificationRepository,
    SqlAlchemyPollRunRepository,
    SqlAlchemyPortalAccountRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork:
    """One SQLite transaction. Not reusable across ``with`` blocks."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.portal_accounts = SqlAlchemyPortalAccountRepository(session)
        self.dossiers = SqlAlchemyDossierRepository(session)
        self.notifications = SqlAlchemyNotificationRepository(session)
        self.poll_runs = SqlAlchemyPollRunRepository(session)
        self.users = SqlAlchemyUserRepository(session)
        self.dossier_work = SqlAlchemyDossierWorkRepository(session)
        self.dossier_notes = SqlAlchemyDossierNoteRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> _UnitOfWorkContext:
        return _UnitOfWorkContext(self._session_factory)


class _UnitOfWorkContext:
    """Opens a session, yields a UnitOfWork, and always closes the session.

    An uncommitted transaction is rolled back on exit so a forgotten
    ``uow.commit()`` never silently leaves partial writes visible to the
    next caller.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        return SqlAlchemyUnitOfWork(self._session)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if self._session.in_transaction():
                self._session.rollback()
        finally:
            self._session.close()
