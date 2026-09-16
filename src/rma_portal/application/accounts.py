"""Local-account use cases: authentication and administration."""

from __future__ import annotations

from datetime import UTC, datetime

from rma_portal.application.ports import PasswordHasher, UnitOfWorkFactory
from rma_portal.domain.enums import Role
from rma_portal.domain.models import CannotDisableSelfError, DuplicateUsernameError, User


class AccountService:
    def __init__(self, uow_factory: UnitOfWorkFactory, password_hasher: PasswordHasher) -> None:
        self._uow_factory = uow_factory
        self._hasher = password_hasher

    def authenticate(self, username: str, password: str) -> User | None:
        with self._uow_factory() as uow:
            user = uow.users.get_by_username(username)
            if user is None or not user.active:
                return None
            if not self._hasher.verify(user.password_hash, password):
                return None
            return user

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role: Role,
    ) -> User:
        normalized = username.strip().casefold()
        now = datetime.now(UTC)
        password_hash = self._hasher.hash(password)
        with self._uow_factory() as uow:
            if uow.users.get_by_username(normalized) is not None:
                raise DuplicateUsernameError(normalized)
            user = uow.users.create(
                User(
                    id=None,
                    username=normalized,
                    display_name=display_name,
                    password_hash=password_hash,
                    role=role,
                    active=True,
                    created_at=now,
                )
            )
            # A new employee must never inherit historical unread notifications.
            uow.notifications.mark_all_existing_as_read_for_user(user.id, now)
            uow.commit()
            return user

    def list_users(self) -> list[User]:
        with self._uow_factory() as uow:
            return uow.users.list_all()

    def set_active(self, *, user_id: int, active: bool, acting_admin_id: int) -> None:
        if user_id == acting_admin_id and not active:
            raise CannotDisableSelfError()
        with self._uow_factory() as uow:
            uow.users.set_active(user_id, active)
            uow.commit()

    def reset_password(self, *, user_id: int, new_password: str) -> None:
        password_hash = self._hasher.hash(new_password)
        with self._uow_factory() as uow:
            uow.users.set_password_hash(user_id, password_hash)
            uow.commit()
