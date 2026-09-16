"""Signed, HTTP-only session cookies (no server-side session store needed).

The cookie payload is a signed, timestamped token carrying only the user
id. ``itsdangerous`` verifies the signature and the twelve-hour max age;
nothing else about the session is persisted.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "rma-portal-session"


class SessionCodec:
    def __init__(self, secret: str, max_age_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=_SALT)
        self._max_age_seconds = max_age_seconds

    def encode(self, user_id: int) -> str:
        return self._serializer.dumps({"uid": user_id})

    def decode(self, token: str) -> int | None:
        try:
            payload = self._serializer.loads(token, max_age=self._max_age_seconds)
        except (BadSignature, SignatureExpired):
            return None
        user_id = payload.get("uid")
        return int(user_id) if isinstance(user_id, int) else None
