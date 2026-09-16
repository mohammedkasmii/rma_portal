from __future__ import annotations

from rma_portal.infrastructure.security.passwords import hash_password, verify_password


class Argon2PasswordHasher:
    """Adapter satisfying ``application.ports.PasswordHasher``."""

    def hash(self, password: str) -> str:
        return hash_password(password)

    def verify(self, password_hash: str, password: str) -> bool:
        return verify_password(password_hash, password)
